# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The process runner at its interface: real processes for `run`, recorded calls for the fake."""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from dokimi import process
from dokimi.process import TIMEOUT_RC, Env, FakeRunner, Limits, reply

PY = sys.executable
INHERIT = Env.inherit()
_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="setsid() and process groups are POSIX")


def _py(code: str, *args: str, **kwargs) -> process.Completed:
    kwargs.setdefault("timeout", 30)
    kwargs.setdefault("env", INHERIT)
    return process.run([PY, "-c", code, *args], **kwargs)


def _alive(pid: int) -> bool:
    """True while ``pid`` runs; a killed process nobody has reaped yet is a zombie, so not alive."""
    if sys.platform == "win32":  # os.kill(pid, 0) succeeds on an exited process whose handle is open
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            return bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)))                 and code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:  # no /proc, or it exited after kill(0): conservatively live
        return True


def _gone_soon(pid: int, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def _wait_for(path: Path, within: float = 10.0) -> str:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if path.is_file() and path.read_text():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} never written")


# ── exit, output, encoding ──────────────────────────────────────────────────


def test_exit_code_and_both_streams():
    done = _py("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)")
    assert (done.returncode, done.stdout, done.stderr) == (3, "out\n", "err\n")
    assert done.timed_out is False and done.ok is False and done.elapsed_sec > 0


def test_output_is_utf8_whatever_the_locale():
    code = "import sys; sys.stdout.buffer.write('é ✓ 日本\\r\\n'.encode() + b'\\xff\\n')"
    done = _py(code, env=Env.inherit({"LC_ALL": "C", "LANG": "C", "PYTHONIOENCODING": "ascii"}))
    assert done.stdout == "é ✓ 日本\n�\n"  # universal newlines, bad bytes replaced
    assert done.stdout_bytes.endswith(b"\xff\n")  # the raw stream is kept for binary callers
    assert _py("import sys; sys.stdout.buffer.write(b'a\\rb')").stdout == "a\nb"  # a lone \r too


def test_input_reaches_stdin_and_stdin_is_closed_otherwise():
    echo = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    assert _py(echo, input=b"\x00bin\n").stdout_bytes == b"\x00bin\n"
    assert _py(echo, input="text é").stdout == "text é"
    start = time.monotonic()
    assert _py(echo, timeout=10).stdout == ""  # EOF at once, not the caller's terminal
    assert time.monotonic() - start < 5


@_POSIX_ONLY
def test_stdin_is_closed_even_when_the_caller_has_an_open_one():
    """pytest already puts /dev/null on fd 0; an open pipe there shows the child does not inherit it."""
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    try:
        done = _py("import sys; print(repr(sys.stdin.read()))", timeout=5)
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end, write_end):
            os.close(fd)
    assert not done.timed_out and done.stdout == "''\n"
    assert process._live == {}  # the finished run is no longer a live group


def test_capture_stdout_false_discards_it():
    done = _py("import sys; print('x'); print('e', file=sys.stderr)", capture_stdout=False)
    assert done.stdout == "" and done.stderr == "e\n"


def test_a_command_that_cannot_start_raises_oserror(tmp_path):
    with pytest.raises(OSError):
        process.run(["no-such-binary-xyz"], timeout=5, env=INHERIT)
    with pytest.raises(OSError):
        _py("pass", cwd=tmp_path / "missing")


@pytest.mark.parametrize("argv, timeout", [([], 5), ([PY], 0), ([PY], -1)])
def test_bad_arguments_raise_valueerror(argv, timeout):
    with pytest.raises(ValueError):
        process.run(argv, timeout=timeout, env=INHERIT)


# ── environment policy ──────────────────────────────────────────────────────


def test_env_policies(monkeypatch):
    monkeypatch.setenv("AB_SECRET", "s3cret")
    monkeypatch.setenv("AB_KEPT", "kept")
    show = "import os, json; print(json.dumps({k: os.environ.get(k) for k in ('AB_SECRET', 'AB_KEPT', 'AB_SET')}))"
    import json

    inherited = json.loads(_py(show, env=Env.inherit({"AB_SET": "1"})).stdout)
    assert inherited == {"AB_SECRET": "s3cret", "AB_KEPT": "kept", "AB_SET": "1"}
    allowed = Env.only(("AB_KEPT", "PATH", "SYSTEMROOT", "AB_ABSENT"), {"AB_SET": "2"})
    assert json.loads(_py(show, env=allowed).stdout) == {"AB_SECRET": None, "AB_KEPT": "kept", "AB_SET": "2"}
    assert "AB_ABSENT" not in allowed.resolve()
    assert Env.only(("A",), {"A": "set wins"}).resolve({"A": "parent"}) == {"A": "set wins"}


# ── timeouts and the process tree ───────────────────────────────────────────


_TREE = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
print("partial out", flush=True)
print("partial err", file=sys.stderr, flush=True)
time.sleep(60)
"""


def test_timeout_kills_grandchildren_and_keeps_partial_output(tmp_path):
    pid_file = tmp_path / "pid"
    start = time.monotonic()
    done = _py(_TREE, str(pid_file), timeout=2)
    assert time.monotonic() - start < 2 + process.DRAIN_GRACE_SEC + 3
    assert done.timed_out is True and done.returncode == TIMEOUT_RC and not done.ok
    assert done.stdout == "partial out\n" and done.stderr == "partial err\n"
    assert _gone_soon(int(pid_file.read_text()))


@_POSIX_ONLY
def test_a_background_job_is_killed_when_the_command_exits(tmp_path):
    """The job holds stdout open; the call returns when the command exits, not
    when the job would, and does not leave the job running."""
    pid_file = tmp_path / "pid"
    start = time.monotonic()
    done = process.run(["bash", "-c", f"sleep 60 & echo $! > '{pid_file}'; echo done"], timeout=30,
                       env=INHERIT)
    assert time.monotonic() - start < process.DRAIN_GRACE_SEC  # killed at the exit, not after the grace
    assert done.ok and done.stdout == "done\n"
    assert _gone_soon(int(pid_file.read_text()))


@_POSIX_ONLY
def test_a_detached_job_that_closed_its_output_is_killed_too(tmp_path):
    pid_file = tmp_path / "pid"
    done = process.run(["bash", "-c", f"sleep 60 >/dev/null 2>&1 & echo $! > '{pid_file}'"], timeout=30,
                       env=INHERIT)
    assert done.ok
    assert _gone_soon(int(pid_file.read_text()))


@_POSIX_ONLY
def test_timeout_returns_despite_a_setsid_child(tmp_path):
    """A child that calls setsid() leaves the group the kill reaches, and is not
    contained. The call still returns at the timeout plus the drain grace instead of
    waiting for that child to close the inherited pipes, keeping what was read."""
    pid_file = tmp_path / "child.pid"
    code = ("import os, sys, time\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"
            "    open(sys.argv[1] + '.tmp', 'w').write(str(os.getpid()))\n"
            "    os.rename(sys.argv[1] + '.tmp', sys.argv[1])\n"
            "    time.sleep(60)\n"
            "    os._exit(0)\n"
            "print('before', flush=True)\n"
            "time.sleep(60)\n")
    start = time.monotonic()
    done = _py(code, str(pid_file), timeout=2)
    elapsed = time.monotonic() - start
    child = int(_wait_for(pid_file))
    try:
        assert _alive(child)  # the escaped child is alive and holds stdout and stderr
        assert done.timed_out and done.stdout == "before\n"
        assert elapsed < 2 + process.DRAIN_GRACE_SEC + 2
    finally:
        os.kill(child, signal.SIGKILL)


def test_interrupt_kills_the_tree_and_propagates(tmp_path, monkeypatch):
    pid_file = tmp_path / "pid"

    def interrupted(*args, **kwargs):
        _wait_for(pid_file)
        raise KeyboardInterrupt

    monkeypatch.setattr(process, "_collect", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _py(_TREE, str(pid_file), timeout=30)
    assert _gone_soon(int(pid_file.read_text()))


# ── term_grace: SIGTERM first, the kill after the grace ───────────────────────

_TRAP_TERM = """
import signal, sys, time
def bye(*_):
    print("cleaned up", flush=True)
    sys.exit(3)
signal.signal(signal.SIGTERM, bye)
print("started", flush=True)
time.sleep(60)
"""


@_POSIX_ONLY
def test_term_grace_lets_the_group_clean_up_on_timeout():
    """What the child prints while it handles the SIGTERM is kept: its output is still read."""
    start = time.monotonic()
    done = _py(_TRAP_TERM, timeout=1, term_grace=10)
    assert time.monotonic() - start < 1 + 5  # it exited on SIGTERM, well inside the grace
    assert done.timed_out and done.returncode == TIMEOUT_RC
    assert done.stdout == "started\ncleaned up\n"


@_POSIX_ONLY
def test_term_grace_ends_in_sigkill_for_a_child_that_ignores_sigterm():
    code = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(60)"
    start = time.monotonic()
    done = _py(code, timeout=1, term_grace=1)
    elapsed = time.monotonic() - start
    assert 1 + 1 - 0.5 < elapsed < 1 + 1 + process.DRAIN_GRACE_SEC + 3
    assert done.timed_out and done.stdout == "up\n"


@_POSIX_ONLY
def test_term_grace_waits_for_the_whole_group_not_only_the_child(tmp_path):
    """The command exits on SIGTERM; a helper of its that ignores SIGTERM still gets the grace,
    then the kill, and is gone when the call returns."""
    pid_file, got_term = tmp_path / "pid", tmp_path / "helper-term"
    helper = ("import signal, sys, time; "
              "signal.signal(signal.SIGTERM, lambda *_: open(sys.argv[1], 'w').write('t')); "
              "time.sleep(60)")
    code = ("import signal, subprocess, sys, time\n"
            f"helper = subprocess.Popen([sys.executable, '-c', {helper!r}, sys.argv[2]])\n"
            "open(sys.argv[1], 'w').write(str(helper.pid))\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            "time.sleep(60)\n")
    start = time.monotonic()
    done = _py(code, str(pid_file), str(got_term), timeout=1, term_grace=1.5)
    elapsed = time.monotonic() - start
    assert done.timed_out and 1 + 1.5 - 0.5 < elapsed < 1 + 1.5 + process.DRAIN_GRACE_SEC + 3
    assert got_term.read_text() == "t"  # the SIGTERM went to the group, not only the leader
    assert _gone_soon(int(pid_file.read_text()))


@_POSIX_ONLY
def test_term_grace_applies_when_the_output_closed_before_the_timeout(tmp_path):
    """A child that closed its output and hangs still gets the SIGTERM and its grace."""
    marker = tmp_path / "term"
    code = ("import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: (open(sys.argv[1], 'w').write('t'), sys.exit(0)))\n"
            "os.close(1); os.close(2)\n"
            "time.sleep(60)\n")
    start = time.monotonic()
    done = _py(code, str(marker), timeout=1, term_grace=10)
    assert done.timed_out and time.monotonic() - start < 1 + 5
    assert marker.read_text() == "t"


@_POSIX_ONLY
def test_term_grace_keeps_waiting_when_the_output_closes_during_the_cleanup(tmp_path):
    marker = tmp_path / "term"
    code = ("import os, signal, sys, time\n"
            "def bye(*_):\n"
            "    os.close(1); os.close(2)\n"
            "    time.sleep(0.5)\n"
            "    open(sys.argv[1], 'w').write('cleaned')\n"
            "    os._exit(0)\n"
            "signal.signal(signal.SIGTERM, bye)\n"
            "time.sleep(60)\n")
    done = _py(code, str(marker), timeout=1, term_grace=10)
    assert done.timed_out and marker.read_text() == "cleaned"


@_POSIX_ONLY
def test_term_grace_applies_to_an_interrupt(tmp_path, monkeypatch):
    marker = tmp_path / "term"
    code = ("import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: (open(sys.argv[1], 'w').write('t'), sys.exit(0)))\n"
            "open(sys.argv[1] + '.up', 'w').write('u')\n"
            "time.sleep(60)\n")

    def interrupted(*args, **kwargs):
        _wait_for(Path(str(marker) + ".up"))
        raise KeyboardInterrupt

    monkeypatch.setattr(process, "_collect", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _py(code, str(marker), timeout=30, term_grace=10)
    assert marker.read_text() == "t"


@_POSIX_ONLY
def test_an_interrupt_waits_for_a_helper_that_outlives_its_leader(tmp_path, monkeypatch):
    up, done = tmp_path / "helper.up", tmp_path / "helper.done"
    helper = ("import signal, sys, time\n"
              "def term(*_):\n"
              "    time.sleep(0.8); open(sys.argv[1], 'w').write('done'); sys.exit(0)\n"
              "signal.signal(signal.SIGTERM, term)\n"
              "open(sys.argv[2], 'w').write('x')\n"
              "time.sleep(60)\n")
    leader = ("import subprocess, sys, time\n"
              f"subprocess.Popen([sys.executable, '-c', {helper!r}, sys.argv[1], sys.argv[2]])\n"
              "time.sleep(60)\n")

    def interrupted(*args, **kwargs):
        _wait_for(up)
        raise KeyboardInterrupt

    monkeypatch.setattr(process, "_collect", interrupted)
    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        _py(leader, str(done), str(up), timeout=30, term_grace=5)
    assert done.read_text() == "done"  # the group got the grace, not only the leader
    assert time.monotonic() - start < 5


@_POSIX_ONLY
def test_an_interrupt_during_the_grace_of_a_closed_output_sends_one_sigterm(tmp_path, monkeypatch):
    log = tmp_path / "term"
    code = ("import os, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: open(sys.argv[1], 'a').write('T'))\n"
            "os.close(1); os.close(2)\n"
            "time.sleep(60)\n")
    real_wait_group, calls = process._wait_group, []

    def wait_group(proc, until):
        calls.append(until)
        if len(calls) == 1:
            time.sleep(0.3)
            raise KeyboardInterrupt
        real_wait_group(proc, until)

    monkeypatch.setattr(process, "_wait_group", wait_group)
    with pytest.raises(KeyboardInterrupt):
        _py(code, str(log), timeout=0.5, term_grace=2)
    assert log.read_text() == "T"
    assert len(calls) == 2 and calls[1] == calls[0]  # the interrupt keeps the first deadline


def test_a_negative_term_grace_is_refused():
    for runner in (process.run, FakeRunner()):
        with pytest.raises(ValueError, match="term_grace"):
            runner(["x"], timeout=1, env=INHERIT, term_grace=-1)


def test_fake_runner_records_the_term_grace():
    fake = FakeRunner()
    fake(["docker", "run"], timeout=5, env=INHERIT, term_grace=3)
    assert fake.calls[0].term_grace == 3


class _Recorded(process.subprocess.Popen):
    """Popen that keeps every child it starts, so a test can look at one run() did not return."""

    started: list[process.subprocess.Popen] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _Recorded.started.append(self)


@pytest.mark.parametrize("where", ["_assign_job", "feeder thread"])
def test_an_error_right_after_popen_kills_the_child(monkeypatch, where):
    """Between Popen and reading the output (registering the child, starting the stdin
    feeder) nothing may leave the child running or in the live groups."""
    def boom(*args, **kwargs):
        raise RuntimeError("can't start new thread")

    _Recorded.started = []
    monkeypatch.setattr(process.subprocess, "Popen", _Recorded)
    if where == "_assign_job":
        monkeypatch.setattr(process, "_assign_job", boom)
    else:
        monkeypatch.setattr(process.threading.Thread, "start", boom)
    with pytest.raises(RuntimeError):
        _py("import time; time.sleep(60)", input=b"x")
    (child,) = [p for p in _Recorded.started if p.args[0] == PY]
    assert child.poll() is not None and process._live == {}
    assert all(p.poll() is not None for p in _Recorded.started)  # includes Windows taskkill


@_POSIX_ONLY
def test_a_signal_between_popen_and_registration_still_kills_the_child(monkeypatch):
    """The handler runs on the main thread, here right after Popen returns and before the
    child is in _live: it must still kill that child, then call the earlier handler."""
    calls = []

    class _SignalledAtStart(_Recorded):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            os.kill(os.getpid(), signal.SIGTERM)

    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(process, "_closing", False)
    _Recorded.started = []
    signal.signal(signal.SIGTERM, lambda signum, frame: calls.append(signum))
    try:
        process.install_signal_handlers([signal.SIGTERM])
        monkeypatch.setattr(process.subprocess, "Popen", _SignalledAtStart)
        start = time.monotonic()
        done = _py("import time; time.sleep(60)", timeout=20)
    finally:
        signal.signal(signal.SIGTERM, before)
    assert done.returncode == -signal.SIGKILL and time.monotonic() - start < 10
    assert calls == [signal.SIGTERM] and process._live == {}
    monkeypatch.setattr(process.subprocess, "Popen", _Recorded)
    assert _py("pass").ok  # the earlier handler returned: the process goes on, and so does run()


def test_installing_twice_wraps_the_handler_once(monkeypatch):
    before = signal.getsignal(signal.SIGTERM)
    try:
        process.install_signal_handlers([signal.SIGTERM])
        once = signal.getsignal(signal.SIGTERM)
        process.install_signal_handlers([signal.SIGTERM])
        assert signal.getsignal(signal.SIGTERM) is once
    finally:
        signal.signal(signal.SIGTERM, before)


def test_the_cli_installs_no_handler_off_the_main_thread(monkeypatch):
    import threading

    from dokimi import cli

    installed, errors = [], []
    monkeypatch.setattr(process, "install_signal_handlers", lambda: installed.append(1))
    monkeypatch.setattr(sys, "argv", ["dokimi", "--help"])

    def call_main():
        try:
            cli.main()
        except SystemExit:  # --help exits 0 after printing
            pass
        except BaseException as exc:  # noqa: BLE001 - reported to the test below
            errors.append(exc)

    worker = threading.Thread(target=call_main)
    worker.start()
    worker.join()
    assert errors == [] and installed == []


# ── a terminating signal to the caller ──────────────────────────────────────

_CALLER = """
import os, signal, sys, threading
from dokimi import process
pid_file, mode = sys.argv[1], sys.argv[2]
if mode == "previous":
    def previous(signum, frame):
        open(pid_file + ".previous", "w").write("called")
        os._exit(7)
    signal.signal(signal.SIGTERM, previous)
if mode == "nohup":
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
process.install_signal_handlers()
worker = threading.Thread(target=process.run, args=([sys.executable, "-c", sys.argv[3], pid_file],),
                          kwargs={"timeout": 120, "env": process.Env.inherit()})
worker.start()
worker.join()
"""


def _caller(tmp_path: Path, mode: str) -> tuple[process.subprocess.Popen, int]:
    """A caller that installed the handlers and runs `_TREE` on a worker thread; and its grandchild."""
    pid_file = tmp_path / "pid"
    env = {**os.environ, "PYTHONPATH": str(Path(process.__file__).resolve().parents[1])}
    caller = process.subprocess.Popen([PY, "-c", _CALLER, str(pid_file), mode, _TREE], env=env)
    return caller, int(_wait_for(pid_file))


@_POSIX_ONLY
def test_sigterm_kills_the_live_groups_then_the_caller_dies_of_it(tmp_path):
    caller, grandchild = _caller(tmp_path, "default")
    try:
        caller.send_signal(signal.SIGTERM)
        assert caller.wait(timeout=30) == -signal.SIGTERM
        assert _gone_soon(grandchild)
    finally:
        caller.kill()
        caller.wait()  # reaped: an unreaped Popen is a ResourceWarning, now an error


@_POSIX_ONLY
def test_sighup_kills_the_live_groups_too(tmp_path):
    caller, grandchild = _caller(tmp_path, "default")
    try:
        caller.send_signal(signal.SIGHUP)
        assert caller.wait(timeout=30) == -signal.SIGHUP
        assert _gone_soon(grandchild)
    finally:
        caller.kill()
        caller.wait()  # reaped: an unreaped Popen is a ResourceWarning, now an error


@_POSIX_ONLY
def test_an_earlier_handler_still_runs_and_an_ignored_sighup_stays_ignored(tmp_path):
    caller, grandchild = _caller(tmp_path, "previous")
    try:
        caller.send_signal(signal.SIGTERM)
        assert caller.wait(timeout=30) == 7 and (tmp_path / "pid.previous").is_file()
        assert _gone_soon(grandchild)
    finally:
        caller.kill()
        caller.wait()  # reaped: an unreaped Popen is a ResourceWarning, now an error
    (tmp_path / "pid").unlink()
    caller, grandchild = _caller(tmp_path, "nohup")
    try:
        caller.send_signal(signal.SIGHUP)
        time.sleep(0.5)
        assert caller.poll() is None and _alive(grandchild)
    finally:
        caller.kill()
        caller.wait()  # reaped: an unreaped Popen is a ResourceWarning, now an error
        os.kill(grandchild, signal.SIGKILL)


def test_importing_installs_no_signal_handler():
    code = "import signal, dokimi.cli, dokimi.process; print(signal.getsignal(signal.SIGTERM))"
    env = Env.inherit({"PYTHONPATH": str(Path(process.__file__).resolve().parents[1])})
    assert _py(code, env=env).stdout.strip() == str(signal.SIG_DFL)


def test_after_kill_children_nothing_new_starts(monkeypatch):
    monkeypatch.setattr(process, "_closing", False)
    process.kill_children()
    with pytest.raises(process.ShuttingDown):
        _py("pass")


def test_a_shutdown_is_not_an_oserror():
    """Call sites read an OSError as "the command is not there"; a shutdown must get past them."""
    assert not issubclass(process.ShuttingDown, Exception)


@_POSIX_ONLY
def test_limits_cap_memory():
    if sys.platform == "darwin":
        with pytest.raises(ValueError, match="RLIMIT_AS.*unsupported"):
            _py("raise AssertionError('must not execute')", limits=Limits(memory_bytes=256 * 1024 * 1024))
        return
    grab = "b = bytearray(512 * 1024 * 1024); print('allocated')"
    capped = _py(grab, limits=Limits(memory_bytes=256 * 1024 * 1024))
    assert capped.returncode != 0 and "MemoryError" in capped.stderr and "allocated" not in capped.stdout
    assert _py(grab).stdout == "allocated\n"  # the same child, uncapped


@_POSIX_ONLY
def test_limits_cap_cpu_time():
    done = _py("while True: pass", timeout=30, limits=Limits(cpu_sec=1))
    assert not done.timed_out and done.returncode == -signal.SIGXCPU


# ── bounded output ──────────────────────────────────────────────────────────

_FLOOD = """
import sys
out, err = sys.stdout.buffer, sys.stderr.buffer
for stream, n in ((out, int(sys.argv[1])), (err, int(sys.argv[2]))):
    stream.write(b"HEAD"); stream.write(b"." * n); stream.write(b"TAIL"); stream.flush()
"""
_MIB = 1 << 20


def _marker(dropped: int) -> bytes:
    return process.TRUNCATION_MARKER.format(dropped=dropped).encode()


def test_a_stream_over_the_capture_limit_keeps_its_head_and_tail():
    done = _py(_FLOOD, str(100), str(20 * _MIB))
    assert done.ok and done.truncated
    assert done.stdout == "HEAD" + "." * 100 + "TAIL"  # under the limit: whole
    err, total, half = done.stderr_bytes, 20 * _MIB + 8, process.CAPTURE_LIMIT // 2
    assert err == err[:half] + _marker(total - process.CAPTURE_LIMIT) + err[-half:]
    assert err.startswith(b"HEAD...") and err.endswith(b"...TAIL")
    assert len(err) == process.CAPTURE_LIMIT + len(_marker(total - process.CAPTURE_LIMIT))


def test_capture_limit_none_keeps_everything_and_a_small_limit_is_honoured():
    whole = _py(_FLOOD, str(3 * _MIB), "0", capture_limit=None)
    assert not whole.truncated and len(whole.stdout_bytes) == 3 * _MIB + 8
    small = _py(_FLOOD, str(3 * _MIB), "0", capture_limit=10)
    assert small.truncated and small.stdout_bytes == b"HEAD." + _marker(3 * _MIB - 2) + b".TAIL"
    assert small.stderr_bytes == b"HEADTAIL"


def test_a_flood_until_the_timeout_is_bounded():
    """The example-runner case: a snippet writing stderr nonstop until it is killed."""
    done = _py("import sys\nwhile True: sys.stderr.write('x' * 65536)", timeout=2)
    assert done.timed_out and done.truncated
    assert len(done.stderr_bytes) < process.CAPTURE_LIMIT + 200


def test_stderr_to_spools_the_whole_stream_and_keeps_head_and_tail(tmp_path):
    log = tmp_path / "stderr.log"
    done = _py(_FLOOD, "5", str(3 * _MIB), stderr_to=log)
    assert log.stat().st_size == 3 * _MIB + 8 and log.read_bytes().endswith(b"TAIL")
    assert done.truncated and done.stdout == "HEAD.....TAIL"
    assert len(done.stderr_bytes) == process.CAPTURE_LIMIT + len(_marker(3 * _MIB + 8 - process.CAPTURE_LIMIT))
    out_log = tmp_path / "stdout.log"
    small = _py(_FLOOD, "5", "0", stdout_to=out_log, capture_limit=None)
    assert out_log.read_bytes() == small.stdout_bytes == b"HEAD.....TAIL" and not small.truncated


def test_the_thread_reader_is_bounded_too(monkeypatch):
    """Windows reads its pipes on threads; the same capture applies."""
    monkeypatch.setattr(process, "_select_pipes", process._thread_pipes)
    done = _py(_FLOOD, "5", str(4 * _MIB))
    assert done.ok and done.truncated and done.stdout == "HEAD.....TAIL"
    assert done.stderr_bytes.startswith(b"HEAD") and done.stderr_bytes.endswith(b"TAIL")
    assert len(done.stderr_bytes) < process.CAPTURE_LIMIT + 200
    late = _py("import time; time.sleep(5)", timeout=1)
    assert late.timed_out and late.returncode == TIMEOUT_RC


def test_a_negative_capture_limit_is_refused():
    with pytest.raises(ValueError):
        _py("pass", capture_limit=-1)


# ── git ─────────────────────────────────────────────────────────────────────


def test_git_never_prompts_and_has_a_timeout(tmp_path):
    fake = FakeRunner(lambda call: reply("abc\n"))
    assert process.git("rev-parse", "HEAD", cwd=tmp_path, runner=fake).stdout == "abc\n"
    (call,) = fake.calls
    assert call.argv == ("git", "rev-parse", "HEAD") and call.cwd == tmp_path
    assert call.env["GIT_TERMINAL_PROMPT"] == "0" and call.timeout == process.GIT_TIMEOUT_SEC
    assert call.capture_limit is None  # git output is data: never cut


_SSH_TAIL = ("fatal: Could not read from remote repository.\n\nPlease make sure you have the correct access "
             "rights\nand the repository exists.\n")
GIT_PROMPT_FAILURES = [  # (stderr, the line that must be shown, a word of the hint)
    ("Cloning into bare repository 'x'...\nHost key verification failed.\n" + _SSH_TAIL,
     "Host key verification failed.", "known_hosts"),
    ("git@github.com: Permission denied (publickey).\n" + _SSH_TAIL,
     "git@github.com: Permission denied (publickey).", "ssh-agent"),
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled\n",
     "fatal: could not read Username for 'https://github.com': terminal prompts disabled", "credential helper"),
    ("fatal: could not read Password for 'https://u@host': terminal prompts disabled\n",
     "fatal: could not read Password for 'https://u@host': terminal prompts disabled", "credential helper"),
]


@pytest.mark.parametrize("stderr, line, hint", GIT_PROMPT_FAILURES)
def test_git_failure_names_the_prompt_git_could_not_show(stderr, line, hint):
    why = process.git_failure(reply(stderr=stderr, returncode=128))
    assert why.startswith(line + " (hint: ") and hint in why


@pytest.mark.parametrize("stderr, why", [
    ("warning: x\nfatal: repository 'https://h/r.git/' not found\nmore\n", "fatal: repository 'https://h/r.git/' not found"),
    ("error: patch failed: a.c:3\nerror: a.c: patch does not apply\n", "error: a.c: patch does not apply"),
    ("one\ntwo\n", "two"),
    ("", "exit 128"),
])
def test_git_failure_otherwise_shows_the_last_fatal_or_error_line(stderr, why):
    assert process.git_failure(reply(stderr=stderr, returncode=128)) == why


def test_git_runs_for_real(tmp_path):
    done = process.git("--version")
    assert done.ok and done.stdout.startswith("git version")


# ── the fake adapter ────────────────────────────────────────────────────────


def test_fake_runner_records_calls_and_answers(tmp_path, monkeypatch):
    monkeypatch.setenv("AB_SECRET", "s")

    def handler(call):
        if call.argv[0] == "missing":
            raise FileNotFoundError(call.argv[0])
        if call.argv[0] == "slow":
            return reply("half", timed_out=True)
        return None

    fake = FakeRunner(handler)
    assert fake(["ok", tmp_path], timeout=1, env=Env.only(("PATH",)), input="hi").ok
    slow = fake(["slow"], timeout=1, env=INHERIT, capture_stdout=False)
    assert slow.timed_out and slow.returncode == TIMEOUT_RC and slow.stdout == "" and slow.argv == ("slow",)
    with pytest.raises(FileNotFoundError):
        fake(["missing"], timeout=1, env=INHERIT)
    with pytest.raises(ValueError):
        fake([], timeout=1, env=INHERIT)
    first = fake.calls[0]
    assert first.argv == ("ok", str(tmp_path)) and first.input == b"hi"
    assert "AB_SECRET" not in first.env and fake.calls[1].env["AB_SECRET"] == "s"
    assert fake.argvs() == [("ok", str(tmp_path)), ("slow",), ("missing",)]


def test_input_larger_than_the_pipe_reaches_a_child_that_reads_late():
    data = b"x" * 300_000
    done = _py("import sys, time; time.sleep(0.6); sys.stdout.buffer.write(sys.stdin.buffer.read())",
               input=data, timeout=20)
    assert not done.timed_out
    assert done.stdout_bytes == data


def test_input_to_a_child_that_never_reads_it_is_not_an_error():
    done = _py("pass", input=b"x" * 2_000_000, timeout=20)
    assert done.returncode == 0 and not done.timed_out


def test_fake_runner_applies_the_capture_limit_and_spools(tmp_path):
    fake = FakeRunner(lambda call: reply("o" * 30, "e" * 30))
    done = fake(["x"], timeout=1, env=INHERIT, capture_limit=10, stderr_to=tmp_path / "err")
    assert done.truncated and done.stdout_bytes == b"o" * 5 + _marker(20) + b"o" * 5
    assert (tmp_path / "err").read_bytes() == b"e" * 30
    assert fake.calls[0].capture_limit == 10 and fake.calls[0].stderr_to == tmp_path / "err"
    assert not fake(["x"], timeout=1, env=INHERIT).truncated


_IGNORE_TERM_CLOSED = ("import os, signal, sys, time\n"
                       "signal.signal(signal.SIGTERM, lambda *_: open(sys.argv[1], 'a').write('T'))\n"
                       "os.close(1); os.close(2)\n"
                       "open(sys.argv[1] + '.pid', 'w').write(str(os.getpid()))\n"
                       "time.sleep(60)\n")


@_POSIX_ONLY
def test_term_grace_is_honoured_then_killed_when_the_output_closed_first(tmp_path):
    log = tmp_path / "term"
    start = time.monotonic()
    done = _py(_IGNORE_TERM_CLOSED, str(log), timeout=0.5, term_grace=1.5)
    elapsed = time.monotonic() - start
    assert done.timed_out and 0.5 + 1.5 - 0.3 < elapsed < 0.5 + 1.5 + 3
    assert log.read_text() == "T"
    assert _gone_soon(int(Path(str(log) + ".pid").read_text()))


@_POSIX_ONLY
def test_term_grace_is_honoured_then_killed_on_an_interrupt(tmp_path, monkeypatch):
    log = tmp_path / "term"

    def interrupted(*args, **kwargs):
        _wait_for(Path(str(log) + ".pid"))
        raise KeyboardInterrupt

    monkeypatch.setattr(process, "_collect", interrupted)
    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        _py(_IGNORE_TERM_CLOSED, str(log), timeout=30, term_grace=1.5)
    elapsed = time.monotonic() - start
    assert 1.5 - 0.3 < elapsed < 1.5 + 5
    assert log.read_text() == "T"
    assert _gone_soon(int(Path(str(log) + ".pid").read_text()))


@_POSIX_ONLY
def test_an_interrupt_during_the_grace_sends_no_second_sigterm(tmp_path, monkeypatch):
    """The timeout's SIGTERM already started the grace; Ctrl-C then waits out only what is left."""
    log = tmp_path / "term"
    code = ("import os, signal, sys, time\n"
            "def term(*_):\n"
            "    open(sys.argv[1], 'a').write('T')\n"
            "    try:\n"
            "        os.close(1); os.close(2)\n"
            "    except OSError:\n"
            "        pass\n"
            "signal.signal(signal.SIGTERM, term)\n"
            "time.sleep(60)\n")
    real_wait_group = process._wait_group
    calls = []

    def wait_group(proc, until):
        calls.append(until)
        if len(calls) == 1:  # the timeout's grace, after the output closed: Ctrl-C now
            time.sleep(0.3)
            raise KeyboardInterrupt
        real_wait_group(proc, until)

    monkeypatch.setattr(process, "_wait_group", wait_group)
    start = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        _py(code, str(log), timeout=0.5, term_grace=2)
    elapsed = time.monotonic() - start
    assert log.read_text() == "T"
    assert calls[1] == calls[0]  # the same grace, not a fresh one
    assert elapsed < 0.5 + 2 + 1.5


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_CHILD_SUBREAPER and /proc are Linux")
def test_a_zombie_group_member_does_not_hold_the_grace_open(tmp_path):
    """As PID 1 or a subreaper, a member orphaned by the leader stays a zombie of ours."""
    script = tmp_path / "subreaper.py"
    script.write_text(
        "import ctypes, sys, time\n"
        "from dokimi import process\n"
        "assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0\n"
        "code = ('import os, signal, subprocess, sys, time\\n'\n"
        "        'subprocess.Popen([sys.executable, \"-c\", \"import time; time.sleep(60)\"])\\n'\n"
        "        'signal.signal(signal.SIGTERM, lambda *_: os._exit(0))\\n'\n"
        "        'time.sleep(60)\\n')\n"
        "start = time.monotonic()\n"
        "done = process.run([sys.executable, '-c', code], timeout=1, env=process.Env.inherit(), term_grace=8)\n"
        "print(done.timed_out, round(time.monotonic() - start, 2))\n",
        encoding="utf-8")
    import subprocess
    root = str(Path(process.__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [root, os.environ.get("PYTHONPATH")]))}
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.split()
    assert out[0] == "True" and float(out[1]) < 1 + 4, out


def test_nothing_else_in_the_package_spawns_a_process():
    """Every spawn site states a timeout and an env policy and kills its tree, because
    it goes through process.run; a direct subprocess call would get none of that."""
    import re

    package = Path(process.__file__).parent
    spawn = re.compile(r"\b(subprocess\.(run|Popen|call|check_call|check_output)|os\.(system|popen|spawn\w*|exec\w*)"
                       r"|asyncio\.create_subprocess_\w+|pty\.spawn)\(")
    found = [f"{path.relative_to(package)}:{n}" for path in sorted(package.rglob("*.py"))
             if path.name != "process.py"
             for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if spawn.search(line) and not line.lstrip().startswith("#")]
    assert found == []


@pytest.mark.parametrize("platform, limits", [
    ("darwin", Limits(memory_bytes=256 * 1024 * 1024)),
    ("win32", Limits(memory_bytes=256 * 1024 * 1024)),
    ("win32", Limits(cpu_sec=1)),
])
def test_unsupported_limits_refuse_before_launch(tmp_path, monkeypatch, platform, limits):
    marker = tmp_path / "started"
    monkeypatch.setattr(process.sys, "platform", platform)
    with pytest.raises(ValueError, match="unsupported"):
        _py("import pathlib, sys; pathlib.Path(sys.argv[1]).touch()", str(marker), limits=limits)
    assert not marker.exists()


@pytest.mark.parametrize("platform, limits", [
    ("darwin", Limits(cpu_sec=2)),
    ("linux", Limits(cpu_sec=2, memory_bytes=256 * 1024 * 1024)),
    ("win32", Limits()),
])
def test_supported_limits_are_not_refused(monkeypatch, platform, limits):
    monkeypatch.setattr(process.sys, "platform", platform)
    assert process.check_limits(limits) is None


@_POSIX_ONLY
def test_a_lower_inherited_hard_limit_caps_the_request(monkeypatch):
    import resource

    applied = {}
    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setattr(resource, "getrlimit", lambda which: (resource.RLIM_INFINITY, 4000)
                        if which == resource.RLIMIT_AS else (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    monkeypatch.setattr(resource, "setrlimit", lambda which, pair: applied.update({which: pair}))
    apply = process._limits_preexec(Limits(cpu_sec=2, memory_bytes=8000))
    assert apply is not None
    apply()
    assert applied == {resource.RLIMIT_AS: (4000, 4000), resource.RLIMIT_CPU: (2, 3)}


@_POSIX_ONLY
def test_rejected_setrlimit_never_launches_user_work(tmp_path, monkeypatch):
    import resource

    def reject(*args):
        raise ValueError("limit rejected")

    monkeypatch.setattr(process.sys, "platform", "linux")
    monkeypatch.setattr(resource, "setrlimit", reject)
    marker = tmp_path / "started"
    with pytest.raises(process.subprocess.SubprocessError, match="preexec_fn"):
        _py("import pathlib, sys; pathlib.Path(sys.argv[1]).touch()", str(marker),
            limits=Limits(memory_bytes=256 * 1024 * 1024))
    assert not marker.exists()


@_POSIX_ONLY
def test_liveness_observer_without_proc_still_sees_live_process(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("no proc filesystem")

    monkeypatch.setattr(Path, "read_text", missing)
    assert _alive(os.getpid())
