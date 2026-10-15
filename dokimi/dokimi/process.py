# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run one external command, with the invariants every spawn site needs.

Callers (git, gh, a doc snippet, a harness profile's CLI) go through :func:`run`,
which holds these invariants:

* **Nothing the run started outlives it.** The child leads its own process group
  (POSIX) or job object (Windows). When the child exits, or the timeout or an
  interrupt fires, the whole group is killed, so a helper it forked cannot keep
  running with its credentials or hold the output pipes open. A process that
  leaves the group (``setsid()``, a daemon) is not contained: the call stops
  waiting for it :data:`DRAIN_GRACE_SEC` after the child is gone.
* **A timeout is a result, not an exception.** ``timed_out`` is set, ``returncode``
  is :data:`TIMEOUT_RC` (124, as ``timeout(1)`` reports it), and the output
  produced before the kill is kept.
* **Output is bytes, read as UTF-8.** ``stdout``/``stderr`` decode with
  replacement and universal newlines, whatever the locale; ``stdout_bytes`` is
  the raw stream for binary callers.
* **The environment is a stated policy.** There is no default ``env``: a call
  site passes :meth:`Env.inherit` (the parent's environment plus overrides) or
  :meth:`Env.only` (an allow-list plus overrides).
* **stdin is closed** (``/dev/null``) unless ``input`` is given, so a child that
  reads stdin gets EOF instead of the caller's terminal.

Errors: a command that cannot be started (missing binary, missing ``cwd``, no
permission) raises :class:`OSError`; a non-zero exit never raises. ``argv`` must
be non-empty and ``timeout`` positive, else :class:`ValueError`.

Two adapters satisfy :class:`Runner`: :func:`run` (real processes) and
:class:`FakeRunner` (records calls and answers from a handler, for tests).
Code that spawns takes a ``runner`` argument defaulting to :func:`run`.
"""

from __future__ import annotations

import dataclasses
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Protocol

TIMEOUT_RC = 124
DRAIN_GRACE_SEC = 2.0
_POLL_SEC = 0.1
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
    """What one run produced. ``returncode`` is :data:`TIMEOUT_RC` when ``timed_out``."""

    argv: tuple[str, ...]
    returncode: int
    stdout_bytes: bytes = b""
    stderr_bytes: bytes = b""
    timed_out: bool = False
    elapsed_sec: float = 0.0

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
) -> Completed:
    """Run ``argv`` (no shell) to completion or ``timeout`` seconds; see the module doc."""
    args = _check(argv, timeout)
    data = input.encode("utf-8") if isinstance(input, str) else input
    start = time.monotonic()
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env.resolve(),
        stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
        stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=_POSIX,
        preexec_fn=_limits_preexec(limits) if _POSIX and limits else None,
    )
    job = _assign_job(proc)
    if data is not None:  # communicate() cannot resume a partial write on a retry: feed stdin on a thread
        sink, proc.stdin = proc.stdin, None
        threading.Thread(target=_feed, args=(sink, data), daemon=True).start()
    try:
        out, err, timed_out = _communicate(proc, None, start + timeout, job)
    except BaseException:
        _kill_tree(proc, job)
        _close_pipes(proc)
        raise
    finally:
        if sys.platform == "win32" and job:
            _kernel32.CloseHandle(job)
    rc = TIMEOUT_RC if timed_out else proc.returncode
    return Completed(tuple(args), rc, out, err, timed_out, time.monotonic() - start)


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


def _check(argv: Sequence[str | os.PathLike[str]], timeout: float) -> list[str]:
    args = [os.fspath(a) for a in argv]
    if not args:
        raise ValueError("empty argv")
    if not timeout > 0:
        raise ValueError(f"timeout must be > 0, got {timeout!r}")
    return args


def _communicate(proc: subprocess.Popen[bytes], data: bytes | None, deadline: float,
                 job: int | None) -> tuple[bytes, bytes, bool]:
    """Read both pipes until the child exits or ``deadline``; then kill what is left."""
    pending: bytes | None = data
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        try:
            out, err = proc.communicate(pending, timeout=max(0.0, min(_POLL_SEC, remaining)))
        except subprocess.TimeoutExpired:
            pending = None  # communicate() keeps the input offset and output read so far
            if proc.poll() is not None:  # the child exited; something it started holds a pipe
                break
            if remaining <= 0:
                timed_out = True
                break
            continue
        _kill_tree(proc, job)  # background jobs that closed their output
        return out or b"", err or b"", False
    _kill_tree(proc, job)
    out, err = _drain(proc)
    return out, err, timed_out


def _drain(proc: subprocess.Popen[bytes]) -> tuple[bytes, bytes]:
    """The output after a kill. A process outside the group may still hold a pipe:
    stop waiting for it after the grace period and keep what was read."""
    try:
        out, err = proc.communicate(timeout=DRAIN_GRACE_SEC)
        return out or b"", err or b""
    except subprocess.TimeoutExpired as exc:
        _close_pipes(proc)
        return exc.output or b"", exc.stderr or b""


def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
    try:
        proc.wait(timeout=DRAIN_GRACE_SEC)
    except subprocess.TimeoutExpired:
        pass
    if _POSIX:  # Windows reader threads own the handles
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe:
                try:
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
    """``git *args`` that never prompts for credentials and always has a timeout."""
    return runner(["git", *args], cwd=cwd, timeout=timeout, env=env, input=input)


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


def reply(stdout: str | bytes = "", stderr: str | bytes = "", returncode: int = 0, *,
          timed_out: bool = False) -> Completed:
    """A :class:`FakeRunner` answer (``argv`` and timing are filled in from the call)."""
    enc = (lambda s: s.encode("utf-8") if isinstance(s, str) else s)
    return Completed((), TIMEOUT_RC if timed_out else returncode, enc(stdout), enc(stderr), timed_out)


class FakeRunner:
    """A :class:`Runner` that spawns nothing: it records every call in ``calls`` and
    answers with ``handler(call)`` (``None`` means exit 0 with no output). The handler
    may raise :class:`OSError` to stand for a command that cannot be started, and may
    write files to stand for the command's side effects."""

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
    ) -> Completed:
        args = _check(argv, timeout)
        data = input.encode("utf-8") if isinstance(input, str) else input
        call = Call(tuple(args), Path(cwd) if cwd is not None else None, env.resolve(), timeout,
                    data, capture_stdout, limits)
        self.calls.append(call)
        answer = self.handler(call) if self.handler else None
        done = answer if answer is not None else reply()
        return dataclasses.replace(done, argv=call.argv,
                                   stdout_bytes=done.stdout_bytes if capture_stdout else b"")

    def argvs(self) -> list[tuple[str, ...]]:
        return [c.argv for c in self.calls]
