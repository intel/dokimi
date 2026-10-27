# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Process handling of the host-subprocess backend: forking, background jobs, timeouts."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from dokimi.metrics.example_runner import ExecutionPolicy, run_example


# Trusted snippets opt out only on macOS; default-cap refusal has dedicated tests.
HOST_MEMORY_MB = None if sys.platform == "darwin" else 512

_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell and process groups")


@_POSIX_ONLY
@pytest.mark.parametrize("lang,code", [
    ("bash", "echo a | cat"),
    ("bash", 'x=$(echo hi); test "$x" = hi'),
    ("python", "import subprocess, sys; subprocess.run([sys.executable, '-c', ''], check=True)"),
    ("python", "import threading; t = threading.Thread(target=lambda: None); t.start(); t.join()"),
])
def test_subprocess_snippet_can_fork(lang, code):
    """A per-UID RLIMIT_NPROC made every fork fail on a host with many processes."""
    res = run_example(lang, code, ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=10))
    assert res.status == "passed", res.error


def test_subprocess_failure_reports_stderr():
    res = run_example(
        "python", "import sys; sys.exit('boom')",
        ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=10),
    )
    assert res.status == "failed"
    assert "Exit code 1" in (res.error or "") and "boom" in (res.error or "")


def _gone_soon(pid: int) -> bool:
    """True once ``pid`` no longer runs. A killed process nobody has reaped yet is a zombie."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        # The process can be reaped between the kill probe and this read; a
        # vanished /proc entry (ENOENT, or ESRCH mid-read) means it is gone.
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            if sys.platform == "linux":
                return True
            state = ""  # macOS has no /proc; keep checking kill(0).
        if state == "Z":
            return True
        time.sleep(0.05)
    return False


@_POSIX_ONLY
def test_subprocess_background_job_is_killed_on_exit(tmp_path):
    pidfile = tmp_path / "pid"
    start = time.monotonic()
    res = run_example(
        "bash", f"sleep 60 & echo $! > '{pidfile}'",
        ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=10),
    )
    assert res.status == "passed", res.error
    assert time.monotonic() - start < 5  # the run does not wait for the background job
    assert _gone_soon(int(pidfile.read_text()))


@_POSIX_ONLY
def test_subprocess_timeout_kills_the_process_group(tmp_path):
    pidfile = tmp_path / "pid"
    res = run_example(
        "bash", f"sleep 60 & echo $! > '{pidfile}'; sleep 60",
        ExecutionPolicy(backend="subprocess", allow_host=True, mem_mb=HOST_MEMORY_MB, timeout=1),
    )
    assert "Timeout" in (res.error or "")
    assert _gone_soon(int(pidfile.read_text()))
