# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run one external command, with the invariants every spawn site needs.

Callers (git, gh, a doc snippet, a harness profile's CLI) go through :func:`run`,
which holds these invariants:

* **Nothing the run started outlives it**, when the call returns or raises. The
  child leads its own process group (POSIX) or job object (Windows). When the
  child exits, or the timeout or an interrupt (``KeyboardInterrupt``) fires, the
  whole group is killed, so a helper it forked cannot keep running with its
  credentials or hold the output pipes open. A process that leaves the group
  (``setsid()``, a daemon) is not contained: the call stops waiting for it
  :data:`DRAIN_GRACE_SEC` after the child is gone.
* **A terminating signal to the caller is handled only where it is installed.**
  The group is the child's own, so a SIGTERM or SIGHUP sent to the caller, or to
  the caller's group as ``timeout(1)`` does, does not reach it. Unhandled, the
  caller dies and the group runs on. :func:`install_signal_handlers` kills every
  live group first; the ``dokimi`` CLI installs it at start-up, importing this
  module does not. A library caller calls it from its main thread, or calls
  :func:`kill_children` from its own handler. Nothing handles ``SIGKILL``.
* **A timeout can stop the group gently.** With ``term_grace`` (POSIX), a timeout or
  an interrupt first sends the group ``SIGTERM`` (once) and gives it that many seconds
  to exit before the kill; on a timeout its output is still read meanwhile: a ``docker`` CLI stops its
  container, ``bench`` its Compose project. Windows terminates the job at once. The
  kill when the child exits, and :func:`kill_children`, are immediate.
* **A timeout is a result, not an exception.** ``timed_out`` is set, ``returncode``
  is :data:`TIMEOUT_RC` (124, as ``timeout(1)`` reports it), and the output
  produced before the kill is kept.
* **Output is bytes, read as UTF-8.** ``stdout``/``stderr`` decode with
  replacement and universal newlines, whatever the locale; ``stdout_bytes`` is
  the raw stream for binary callers.
* **Captured output is bounded.** Each stream keeps at most ``capture_limit``
  bytes in memory (:data:`CAPTURE_LIMIT`, 1 MiB, by default): its first and last
  half, joined by :data:`TRUNCATION_MARKER`, with ``truncated`` set on the result.
  ``capture_limit=None`` keeps everything, for output the caller parses (git data).
  ``stdout_to``/``stderr_to`` send a stream to a file instead of a pipe, so the
  whole log is on disk; the result still holds its bounded head and tail.
* **The environment is a stated policy.** There is no default ``env``: a call
  site passes :meth:`Env.inherit` (the parent's environment plus overrides) or
  :meth:`Env.only` (an allow-list plus overrides).
* **stdin is closed** (``/dev/null``) unless ``input`` is given, so a child that
  reads stdin gets EOF instead of the caller's terminal.

Errors: a command that cannot be started (missing binary, missing ``cwd``, no
permission) raises :class:`OSError`; a non-zero exit never raises. After
:func:`kill_children`, a call raises :class:`ShuttingDown` instead of starting anything. ``argv`` must
be non-empty and ``timeout`` positive, else :class:`ValueError`.

Two adapters satisfy :class:`Runner`: :func:`run` (real processes) and
:class:`FakeRunner` (records calls and answers from a handler, for tests).
Code that spawns takes a ``runner`` argument defaulting to :func:`run`.
"""

from __future__ import annotations

import dataclasses
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import IO, Protocol

TIMEOUT_RC = 124
DRAIN_GRACE_SEC = 2.0
CAPTURE_LIMIT = 1 << 20
TRUNCATION_MARKER = "\n[... {dropped} bytes not kept: output over the capture limit ...]\n"
_POLL_SEC = 0.1
_CHUNK = 1 << 16
_POSIX = sys.platform != "win32"

if sys.platform == "win32":
    import ctypes

    _kernel32 = ctypes.WinDLL("kernel32")
    _kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    _kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
    _kernel32.TerminateJobObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)


@dataclass(frozen=True)
class Env:
    """The child's environment: ``allow`` names taken from the parent (all of them
    when ``allow`` is ``None``), then ``set`` applied on top."""

    allow: tuple[str, ...] | None
    set: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def inherit(cls, set: Mapping[str, str] | None = None) -> Env:
        """The parent's whole environment, plus ``set``."""
        return cls(None, dict(set or {}))

    @classmethod
    def only(cls, allow: Sequence[str] = (), set: Mapping[str, str] | None = None) -> Env:
        """Only the ``allow``-listed names the parent has, plus ``set``."""
        return cls(tuple(allow), dict(set or {}))

    def resolve(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        source = os.environ if base is None else base
        env = dict(source) if self.allow is None else {k: source[k] for k in self.allow if k in source}
        env.update(self.set)
        return env


@dataclass(frozen=True)
class Limits:
    """POSIX resource limits applied in the child (ignored on Windows)."""

    cpu_sec: int | None = None
    memory_bytes: int | None = None


@dataclass(frozen=True)
class Completed:
    """What one run produced. ``returncode`` is :data:`TIMEOUT_RC` when ``timed_out``;
    ``truncated`` when a stream was over the capture limit and its middle was dropped."""

    argv: tuple[str, ...]
    returncode: int
    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    timed_out: bool = False
    elapsed_sec: float = 0.0
    truncated: bool = False

    @cached_property
    def stdout(self) -> str:
        return _decode(self.stdout_bytes)

    @cached_property
    def stderr(self) -> str:
        return _decode(self.stderr_bytes)

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class Runner(Protocol):
    def __call__(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        env: Env,
        cwd: str | os.PathLike[str] | None = None,
        input: str | bytes | None = None,
        capture_stdout: bool = True,
        limits: Limits | None = None,
        capture_limit: int | None = CAPTURE_LIMIT,
        stdout_to: str | os.PathLike[str] | None = None,
        stderr_to: str | os.PathLike[str] | None = None,
        term_grace: float = 0.0,
    ) -> Completed: ...


def run(
    argv: Sequence[str | os.PathLike[str]],
    *,
    timeout: float,
    env: Env,
    cwd: str | os.PathLike[str] | None = None,
    input: str | bytes | None = None,
    capture_stdout: bool = True,
    limits: Limits | None = None,
    capture_limit: int | None = CAPTURE_LIMIT,
    stdout_to: str | os.PathLike[str] | None = None,
    stderr_to: str | os.PathLike[str] | None = None,
    term_grace: float = 0.0,
) -> Completed:
    """Run ``argv`` (no shell) to completion or ``timeout`` seconds; see the module doc."""
    args = _check(argv, timeout, capture_limit, term_grace)
    data = input.encode("utf-8") if isinstance(input, str) else input
    with _Spool(stdout_to) as out_file, _Spool(stderr_to) as err_file:
        start = time.monotonic()
        try:
            proc, job = _spawn(
                args,
                cwd=cwd,
                env=env.resolve(),
                stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
                stdout=out_file or (subprocess.PIPE if capture_stdout else subprocess.DEVNULL),
                stderr=err_file or subprocess.PIPE,
                start_new_session=_POSIX,
                preexec_fn=_limits_preexec(limits) if _POSIX and limits else None,
            )
        except BaseException:
            _replay_deferred_signals()  # no child started: a signal that came meanwhile acts now
            raise
    # From here the child is in _live: whatever raises below, the except kills its group.
    try:
        _replay_deferred_signals()
        if data is not None:  # a child that reads late must still get all of it: feed stdin on a thread
            sink, proc.stdin = proc.stdin, None
            try:
                threading.Thread(target=_feed, args=(sink, data), daemon=True).start()
            except BaseException:
                if sink:
                    sink.close()
                raise
        out, err = _Capture(capture_limit), _Capture(capture_limit)
        pipes = [(pipe, cap) for pipe, cap in ((proc.stdout, out), (proc.stderr, err)) if pipe]
        timed_out = _collect(proc, pipes, start + timeout, job, term_grace)
    except BaseException:
        try:
            _terminate(proc, term_grace)
        finally:
            _kill_tree(proc, job)
        raise
    finally:
        _close_pipes(proc)
        _term_until.pop(proc.pid, None)
        with _live_lock:
            _live.pop(proc.pid, None)
            if sys.platform == "win32" and job:
                _kernel32.CloseHandle(job)
    out_bytes, out_cut = _read_spool(stdout_to, capture_limit) if stdout_to else out.value()
    err_bytes, err_cut = _read_spool(stderr_to, capture_limit) if stderr_to else err.value()
    if not capture_stdout:
        out_bytes, out_cut = b"", False
    rc = TIMEOUT_RC if timed_out else proc.returncode
    return Completed(tuple(args), rc, out_bytes, err_bytes, timed_out, time.monotonic() - start,
                     out_cut or err_cut)


# ── the groups alive right now, for a terminating signal ────────────────────

_live: dict[int, tuple[subprocess.Popen[bytes], int | None]] = {}  # pid -> (process, job object)
#: pid -> when the grace of the SIGTERM already sent to its group ends.
_term_until: dict[int, float] = {}
_live_lock = threading.RLock()  # re-entrant: a handler runs on the main thread, maybe inside run()
_closing = False
# Per thread, so only the main thread's (where Python runs signal handlers) is ever read
# by a handler: whether it is between Popen and registering the child in _live, and the
# signals that arrived meanwhile, replayed once the child is registered.
_spawning = threading.local()


class ShuttingDown(BaseException):  # noqa: N818 - named for what it means, like KeyboardInterrupt
    """Raised by :func:`run` after :func:`kill_children`: the caller is on its way out, so
    nothing new starts. Not an :class:`OSError` (nor an :class:`Exception`), so a call site
    that reads a command it cannot start as "no answer" does not swallow it."""


def kill_children() -> None:
    """Kill the process group (job object) of every :func:`run` in progress, in any thread,
    and make later calls raise :class:`ShuttingDown` instead of starting anything.
    For a caller on its way out; :func:`install_signal_handlers` calls it."""
    global _closing
    locked = _live_lock.acquire(timeout=DRAIN_GRACE_SEC)  # let a thread inside Popen register its child
    try:
        _closing = True
        for proc, job in list(_live.values()):
            _kill_tree(proc, job)
    finally:
        if locked:
            _live_lock.release()


def install_signal_handlers(signums: Sequence[int] | None = None) -> None:
    """On SIGTERM and SIGHUP (or ``signums``): :func:`kill_children`, then call the handler
    that was installed before, or, if that was the default, die of the signal as before
    (exit status 128 + the signal number). When the earlier handler returns, the process
    carries on and :func:`run` starts processes again. A signal the process ignores (SIGHUP
    under ``nohup``) stays ignored, and one already handled here is not wrapped twice. Call
    it from the main thread: ``signal.signal`` raises :class:`ValueError` elsewhere. The
    ``dokimi`` CLI calls it at start-up."""
    if signums is None:
        signums = [s for s in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGHUP", None)) if s]
    for signum in signums:
        previous = signal.getsignal(signum)
        if previous != signal.SIG_IGN and not getattr(previous, "_kills_children", False):
            signal.signal(signum, _on_signal(previous))


def _on_signal(previous: object) -> Callable[[int, object], None]:
    def handler(signum: int, frame: object) -> None:
        global _closing
        if getattr(_spawning, "active", False):  # the new child is not in _live yet: kill it once it is
            _spawning.deferred.append((handler, signum, frame))
            return
        kill_children()
        if callable(previous):
            previous(signum, frame)
            with _live_lock:  # the earlier handler let the process go on: so may run()
                _closing = False
        else:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    handler._kills_children = True  # type: ignore[attr-defined]
    return handler


def _spawn(args: list[str], **popen: object) -> tuple[subprocess.Popen[bytes], int | None]:
    """Start the child and register it in :data:`_live` as one step: a signal handled on this
    thread in between is deferred until the child is registered (:func:`_replay_deferred_signals`),
    and an error in between kills the child."""
    with _live_lock:
        if _closing:  # a terminating signal is being handled: start nothing new
            raise ShuttingDown("not starting a process: shutting down on a signal")
        _spawning.active, _spawning.deferred = True, getattr(_spawning, "deferred", [])
        try:
            proc = subprocess.Popen(args, **popen)  # type: ignore[call-overload]
            job = None
            try:
                job = _assign_job(proc)
                _live[proc.pid] = (proc, job)
            except BaseException:
                _kill_tree(proc, job)
                proc.wait()
                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    if pipe:
                        pipe.close()
                raise
        finally:
            _spawning.active = False
    return proc, job


def _replay_deferred_signals() -> None:
    """Run the handlers :func:`_spawn` deferred on this thread, now that the child is in _live."""
    deferred = getattr(_spawning, "deferred", [])
    while deferred:
        handler, signum, frame = deferred.pop(0)
        handler(signum, frame)


class _Capture:
    """One captured stream: all of it, or only its first and last ``limit // 2`` bytes."""

    def __init__(self, limit: int | None) -> None:
        self.head_cap = None if limit is None else limit // 2
        self.tail_cap = 0 if limit is None else limit - limit // 2
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self._lock = threading.Lock()  # the Windows reader threads add while the caller may read

    def add(self, chunk: bytes) -> None:
        with self._lock:
            self.total += len(chunk)
            if self.head_cap is None:
                self.head += chunk
                return
            room = self.head_cap - len(self.head)
            if room > 0:
                self.head += chunk[:room]
                chunk = chunk[room:]
            self.tail += chunk
            if len(self.tail) > self.tail_cap:
                del self.tail[:len(self.tail) - self.tail_cap]

    def value(self) -> tuple[bytes, bool]:
        """The kept bytes, with the marker where the middle was dropped, and whether it was."""
        with self._lock:
            return _join(bytes(self.head), bytes(self.tail), self.total)


def _join(head: bytes, tail: bytes, total: int) -> tuple[bytes, bool]:
    dropped = total - len(head) - len(tail)
    if dropped <= 0:
        return head + tail, False
    return head + TRUNCATION_MARKER.format(dropped=dropped).encode("utf-8") + tail, True


class _Spool:
    """``with _Spool(path) as f``: ``path`` opened for the child to write (``None`` passes through)."""

    def __init__(self, path: str | os.PathLike[str] | None) -> None:
        self.file = open(path, "wb") if path is not None else None  # noqa: SIM115 - closed in __exit__

    def __enter__(self) -> IO[bytes] | None:
        return self.file

    def __exit__(self, *exc: object) -> None:
        if self.file:  # the child holds its own copy of the descriptor
            self.file.close()


def _read_spool(path: str | os.PathLike[str], limit: int | None) -> tuple[bytes, bool]:
    """A spooled stream's bounded head and tail, read back without loading the whole file."""
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if limit is None or size <= limit:
            return fh.read(), False
        head = fh.read(limit // 2)
        tail_len = limit - limit // 2
        fh.seek(size - tail_len)
        return _join(head, fh.read(tail_len), size)


def _feed(sink, data: bytes) -> None:
    """Write ``data`` to the child's stdin and close it; a child that stops reading is not an error."""
    try:
        sink.write(data)
    except OSError:
        pass
    finally:
        try:
            sink.close()
        except OSError:
            pass


def _check(argv: Sequence[str | os.PathLike[str]], timeout: float, capture_limit: int | None,
           term_grace: float = 0.0) -> list[str]:
    args = [os.fspath(a) for a in argv]
    if not args:
        raise ValueError("empty argv")
    if not timeout > 0:
        raise ValueError(f"timeout must be > 0, got {timeout!r}")
    if capture_limit is not None and capture_limit < 0:
        raise ValueError(f"capture_limit must be >= 0 or None, got {capture_limit!r}")
    if not term_grace >= 0:
        raise ValueError(f"term_grace must be >= 0, got {term_grace!r}")
    return args


_Pipes = Sequence[tuple[IO[bytes], _Capture]]


def _collect(proc: subprocess.Popen[bytes], pipes: _Pipes, deadline: float, job: int | None,
             term_grace: float = 0.0) -> bool:
    """Read the pipes until the child exits or ``deadline`` passes (then, with ``term_grace``,
    until its group exits on ``SIGTERM`` or the grace ends); then kill what is left of its
    group. A process outside the group may still hold a pipe: stop reading it
    :data:`DRAIN_GRACE_SEC` after the kill. Returns whether the deadline fired."""
    timed_out = (_select_pipes if _POSIX else _thread_pipes)(proc, pipes, deadline, job, term_grace)
    _kill_tree(proc, job)  # also background jobs that closed their output
    return timed_out


def _select_pipes(proc: subprocess.Popen[bytes], pipes: _Pipes, deadline: float, job: int | None,
                  term_grace: float = 0.0) -> bool:
    timed_out = termed = False
    stop_at = deadline  # when the group gets its next signal: SIGTERM (with term_grace), then the kill
    killed_at: float | None = None
    with selectors.DefaultSelector() as sel:
        for pipe, cap in pipes:
            sel.register(pipe, selectors.EVENT_READ, cap)
        while sel.get_map():
            now = time.monotonic()
            if killed_at is None:
                # It exited (after a SIGTERM: its whole group did), or it is time for the next signal.
                done = proc.poll() is not None and not (termed and _group_alive(proc))
                if not done and now >= stop_at:
                    timed_out = True
                    if not termed and term_grace > 0 and _signal_group(proc, signal.SIGTERM):
                        termed, stop_at = True, now + term_grace
                        _term_until[proc.pid] = stop_at  # an interrupt now waits out this grace only
                    else:
                        done = True
                if done:
                    _kill_tree(proc, job)  # whatever still holds a pipe in the group goes too
                    killed_at = now
            if killed_at is not None and now >= killed_at + DRAIN_GRACE_SEC:
                break
            until = killed_at + DRAIN_GRACE_SEC if killed_at is not None else min(stop_at, now + _POLL_SEC)
            for key, _ in sel.select(max(0.0, until - now)):
                chunk = os.read(key.fd, _CHUNK)
                if chunk:
                    key.data.add(chunk)
                else:
                    sel.unregister(key.fileobj)
    if killed_at is None:  # the output closed first: wait for the child itself
        if termed:
            _wait_group(proc, stop_at)
        else:
            timed_out = _wait(proc, deadline)
            if timed_out:
                _terminate(proc, term_grace)
    return timed_out


def _thread_pipes(proc: subprocess.Popen[bytes], pipes: _Pipes, deadline: float, job: int | None,
                  term_grace: float = 0.0) -> bool:
    """Windows has no select() on pipes: one reader thread per pipe. No SIGTERM there either:
    ``term_grace`` is ignored and the job is terminated at once."""
    readers = [threading.Thread(target=_pump, args=(pipe, cap), daemon=True) for pipe, cap in pipes]
    for reader in readers:
        reader.start()
    timed_out = _wait(proc, deadline)
    _kill_tree(proc, job)
    grace_end = time.monotonic() + DRAIN_GRACE_SEC
    for reader in readers:
        reader.join(max(0.0, grace_end - time.monotonic()))
    return timed_out


def _pump(pipe: IO[bytes], cap: _Capture) -> None:
    try:
        while chunk := pipe.read1(_CHUNK):  # type: ignore[attr-defined]
            cap.add(chunk)
    except (OSError, ValueError):  # the pipe was closed under the reader
        pass


def _wait(proc: subprocess.Popen[bytes], deadline: float) -> bool:
    """Wait for the child until ``deadline`` in short steps (so an interrupt is seen); True on timeout."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return proc.poll() is None
        try:
            proc.wait(timeout=min(_POLL_SEC, remaining))
            return False
        except subprocess.TimeoutExpired:
            continue


def _terminate(proc: subprocess.Popen[bytes], grace: float) -> None:
    """With a ``grace`` (POSIX): ``SIGTERM`` the group and wait up to ``grace`` seconds for all
    of it to exit. The caller kills what is left. A group already sent ``SIGTERM`` (an
    interrupt during the timeout's grace) is not signalled again: the wait ends with the
    grace it already has."""
    until = _term_until.get(proc.pid)
    if until is not None:
        _wait_group(proc, until)
    elif _POSIX and grace > 0 and _signal_group(proc, signal.SIGTERM):
        _term_until[proc.pid] = until = time.monotonic() + grace
        _wait_group(proc, until)


def _wait_group(proc: subprocess.Popen[bytes], until: float) -> None:
    """Wait until the child and the rest of its group have exited, or until ``until``."""
    while (remaining := until - time.monotonic()) > 0:
        if proc.poll() is not None and not _group_alive(proc):  # reap the leader, or it keeps the group
            return
        time.sleep(min(_POLL_SEC, remaining))


def _group_alive(proc: subprocess.Popen[bytes]) -> bool:
    """Whether the child's process group has a member that has not exited.

    ``killpg(pgid, 0)`` also counts a zombie nobody has reaped yet, which is what a
    member orphaned to this process stays when it is PID 1 or a subreaper. On Linux
    the members' states are read from ``/proc`` and zombies are left out.
    """
    if not _signal_group(proc, 0):
        return False
    if sys.platform != "linux":
        return True
    try:
        entries = os.listdir("/proc")
    except OSError:
        return True
    pgid = str(proc.pid)
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", encoding="ascii", errors="replace") as f:
                stat = f.read()
        except OSError:  # it exited meanwhile
            continue
        fields = stat[stat.rfind(")") + 2:].split()  # state, ppid, pgrp, ...
        if len(fields) > 2 and fields[2] == pgid and fields[0] not in ("Z", "X"):
            return True
    return False


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> bool:
    """Send ``sig`` to the child's process group (0: only check it has a member); False when
    there is none left (POSIX only)."""
    if sys.platform == "win32":  # pragma: no cover - Windows has no process groups to signal
        return False
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        return False
    return True


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.wait(timeout=DRAIN_GRACE_SEC)
    except subprocess.TimeoutExpired:
        pass
    if _POSIX:  # Windows reader threads own the handles
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass


def _assign_job(proc: subprocess.Popen[bytes]) -> int | None:
    if sys.platform == "win32":
        # taskkill /T misses a helper whose parent already exited; a job object does not.
        job = _kernel32.CreateJobObjectW(None, None)
        if job and not _kernel32.AssignProcessToJobObject(job, int(proc._handle)):  # type: ignore[attr-defined]
            _kernel32.CloseHandle(job)
            job = None
        return job
    return None


def _kill_tree(proc: subprocess.Popen[bytes], job: int | None) -> None:
    if sys.platform == "win32" and job:
        _kernel32.TerminateJobObject(job, 1)
    elif sys.platform == "win32":
        if proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:  # the group is already empty
            pass
    if proc.poll() is None:
        proc.kill()


def _limits_preexec(limits: Limits) -> Callable[[], None]:
    import resource

    def apply() -> None:
        if limits.cpu_sec is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_sec, limits.cpu_sec + 1))
        if limits.memory_bytes is not None:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
            except (ValueError, OSError):  # pragma: no cover - platform dependent
                pass

    return apply


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


# ── git ──────────────────────────────────────────────────────────────────────

GIT_TIMEOUT_SEC = 300.0
GIT_ENV = Env.inherit({"GIT_TERMINAL_PROMPT": "0"})


def git(*args: str, cwd: str | os.PathLike[str] | None = None, timeout: float = GIT_TIMEOUT_SEC,
        input: str | bytes | None = None, env: Env = GIT_ENV, runner: Runner = run) -> Completed:
    """``git *args`` that never prompts for credentials and always has a timeout. Its output
    is data the caller parses, so it is captured whole (``capture_limit=None``)."""
    return runner(["git", *args], cwd=cwd, timeout=timeout, env=env, input=input, capture_limit=None)


# What git or ssh prints when it would have prompted, and what to do instead. git runs with
# GIT_TERMINAL_PROMPT=0, stdin closed and, in its own session, no terminal: nothing can be typed.
_GIT_HINTS = (
    ("host key verification failed",
     "the remote's SSH host key is not in ~/.ssh/known_hosts and cannot be confirmed here: "
     "add it (ssh-keyscan <host> >> ~/.ssh/known_hosts, after checking the fingerprint) or use the https:// URL"),
    ("permission denied (publickey",
     "no SSH key the remote accepts is available without a prompt: load it into ssh-agent (ssh-add) "
     "or use the https:// URL"),
    *((needle, "the remote wants credentials and git does not prompt here: store a token for the "
               "https:// URL in a credential helper (for GitHub: gh auth setup-git) or use an SSH URL "
               "with a key in ssh-agent")
      for needle in ("could not read username", "could not read password", "terminal prompts disabled")),
)


def git_failure(done: Completed) -> str:
    """The line of a failed git call's output that says why, and a hint when it is a known
    prompt git could not show (an unknown host key, an SSH key or credentials it would have
    asked for). Otherwise the last ``fatal:`` or ``error:`` line, else the last line."""
    lines = [ln.strip() for ln in (done.stderr or done.stdout).splitlines() if ln.strip()]
    for needle, hint in _GIT_HINTS:
        for line in lines:
            if needle in line.lower():
                return f"{line} (hint: {hint})"
    for prefix in ("fatal:", "error:"):
        found = [ln for ln in lines if ln.lower().startswith(prefix)]
        if found:
            return found[-1]
    return lines[-1] if lines else f"exit {done.returncode}"


# ── test adapter ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Call:
    """One recorded :class:`FakeRunner` call, with the environment resolved."""

    argv: tuple[str, ...]
    cwd: Path | None
    env: dict[str, str]
    timeout: float
    input: bytes | None
    capture_stdout: bool = True
    limits: Limits | None = None
    capture_limit: int | None = CAPTURE_LIMIT
    stdout_to: Path | None = None
    stderr_to: Path | None = None
    term_grace: float = 0.0


def reply(stdout: str | bytes = "", stderr: str | bytes = "", returncode: int = 0, *,
          timed_out: bool = False) -> Completed:
    """A :class:`FakeRunner` answer (``argv`` and timing are filled in from the call)."""
    enc = (lambda s: s.encode("utf-8") if isinstance(s, str) else s)
    return Completed((), TIMEOUT_RC if timed_out else returncode, enc(stdout), enc(stderr), timed_out)


class FakeRunner:
    """A :class:`Runner` that spawns nothing: it records every call in ``calls`` and
    answers with ``handler(call)`` (``None`` means exit 0 with no output). The handler
    may raise :class:`OSError` to stand for a command that cannot be started, and may
    write files to stand for the command's side effects. The answer goes through the
    same capture limit and ``stdout_to``/``stderr_to`` files as a real run."""

    def __init__(self, handler: Callable[[Call], Completed | None] | None = None) -> None:
        self.handler = handler
        self.calls: list[Call] = []

    def __call__(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        timeout: float,
        env: Env,
        cwd: str | os.PathLike[str] | None = None,
        input: str | bytes | None = None,
        capture_stdout: bool = True,
        limits: Limits | None = None,
        capture_limit: int | None = CAPTURE_LIMIT,
        stdout_to: str | os.PathLike[str] | None = None,
        stderr_to: str | os.PathLike[str] | None = None,
        term_grace: float = 0.0,
    ) -> Completed:
        args = _check(argv, timeout, capture_limit, term_grace)
        data = input.encode("utf-8") if isinstance(input, str) else input
        path = (lambda p: Path(p) if p is not None else None)
        call = Call(tuple(args), path(cwd), env.resolve(), timeout, data, capture_stdout, limits,
                    capture_limit, path(stdout_to), path(stderr_to), term_grace)
        self.calls.append(call)
        answer = self.handler(call) if self.handler else None
        done = answer if answer is not None else reply()
        out, out_cut = self._capture(done.stdout_bytes, call.stdout_to, capture_limit)
        err, err_cut = self._capture(done.stderr_bytes, call.stderr_to, capture_limit)
        if not capture_stdout:
            out, out_cut = b"", False
        return dataclasses.replace(done, argv=call.argv, stdout_bytes=out, stderr_bytes=err,
                                   truncated=done.truncated or out_cut or err_cut)

    @staticmethod
    def _capture(data: bytes, to: Path | None, limit: int | None) -> tuple[bytes, bool]:
        if to is not None:
            to.write_bytes(data)
        cap = _Capture(limit)
        cap.add(data)
        return cap.value()

    def argvs(self) -> list[tuple[str, ...]]:
        return [c.argv for c in self.calls]
