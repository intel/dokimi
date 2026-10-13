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
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError:  # no /proc (macOS): kill(0) succeeded
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


def test_input_reaches_stdin_and_stdin_is_closed_otherwise():
    echo = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    assert _py(echo, input=b"\x00bin\n").stdout_bytes == b"\x00bin\n"
    assert _py(echo, input="text é").stdout == "text é"
    start = time.monotonic()
    assert _py(echo, timeout=10).stdout == ""  # EOF at once, not the caller's terminal
    assert time.monotonic() - start < 5


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
    assert time.monotonic() - start < 5
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
    real = process.subprocess.Popen.communicate
    calls = {"n": 0}

    def interrupted(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            _wait_for(pid_file)
            raise KeyboardInterrupt
        return real(self, *args, **kwargs)

    monkeypatch.setattr(process.subprocess.Popen, "communicate", interrupted)
    with pytest.raises(KeyboardInterrupt):
        _py(_TREE, str(pid_file), timeout=30)
    assert _gone_soon(int(pid_file.read_text()))


@_POSIX_ONLY
def test_limits_cap_cpu_time():
    done = _py("while True: pass", timeout=30, limits=Limits(cpu_sec=1))
    assert not done.timed_out and done.returncode == -signal.SIGXCPU


# ── git ─────────────────────────────────────────────────────────────────────


def test_git_never_prompts_and_has_a_timeout(tmp_path):
    fake = FakeRunner(lambda call: reply("abc\n"))
    assert process.git("rev-parse", "HEAD", cwd=tmp_path, runner=fake).stdout == "abc\n"
    (call,) = fake.calls
    assert call.argv == ("git", "rev-parse", "HEAD") and call.cwd == tmp_path
    assert call.env["GIT_TERMINAL_PROMPT"] == "0" and call.timeout == process.GIT_TIMEOUT_SEC


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
