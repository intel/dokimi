# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The /proc reader the SIGTERM reach test uses to tell a running process from a dead one."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

import tests.pid_state as pid_state_module
from tests.pid_state import describe, pid_alive, pid_state

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX os.kill(pid, 0)")
def test_a_live_pid_without_proc_is_alive_and_a_gone_one_is_not(monkeypatch):
    def no_proc(self, *args, **kwargs):
        raise FileNotFoundError(str(self))

    monkeypatch.setattr(pid_state_module.Path, "read_text", no_proc)  # a host without /proc
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert pid_state(child.pid) == "?"
        assert pid_alive(child.pid)
    finally:
        child.kill()
        child.wait()
    assert pid_state(child.pid) is None
    assert not pid_alive(child.pid)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_running_zombie_and_reaped_child_are_told_apart():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        assert pid_alive(child.pid)
        assert f"pid {child.pid} state " in describe(child.pid)
        assert "time.sleep(60)" in describe(child.pid)
        child.kill()
        deadline = time.monotonic() + 30
        while pid_state(child.pid) != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_state(child.pid) == "Z"  # killed, not reaped yet
        assert not pid_alive(child.pid)
    finally:
        child.kill()
        child.wait()
    assert pid_state(child.pid) is None  # reaped: gone, not "alive because /proc cannot say"
    assert not pid_alive(child.pid)
    assert describe(child.pid) == f"pid {child.pid} state None"
