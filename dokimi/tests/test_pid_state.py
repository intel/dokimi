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


def test_a_pid_reaped_between_the_probe_and_the_proc_read_is_gone_not_alive(monkeypatch):
    def no_proc(self, *args, **kwargs):
        raise FileNotFoundError(str(self))

    probes = iter([None, ProcessLookupError()])  # the first probe finds it, the recheck does not

    def kill(pid, sig):
        outcome = next(probes)
        if outcome is not None:
            raise outcome

    monkeypatch.setattr(pid_state_module.Path, "read_text", no_proc)
    monkeypatch.setattr(pid_state_module.os, "kill", kill)
    assert pid_state(4242) is None

    probes = iter([None, ProcessLookupError()])
    assert not pid_alive(4242)


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
