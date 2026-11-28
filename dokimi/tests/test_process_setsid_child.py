# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`process.run` returns after the drain grace although a child that left the process group holds the pipes."""

from __future__ import annotations

import threading

import pytest

from tests.test_process import _POSIX_ONLY, _alive, _fire_timeout_once_written, _py
from tests.waiting import wait_for_event, wait_until


@_POSIX_ONLY
@pytest.mark.timeout(200)  # 120 s for the child to escape + 30 s for the call to return + 30 s to clean up
def test_timeout_returns_despite_a_setsid_child(tmp_path, monkeypatch):
    """A child that calls setsid() leaves the group the kill reaches. The call still returns after the drain
    grace, not when that child closes the inherited pipes, keeping what was read. The timeout fires once the
    child has escaped, and both processes live until the test writes `stop` (10 minutes at most): no clock decides it."""
    pid_file, said, stop = tmp_path / "child.pid", tmp_path / "said", tmp_path / "stop"
    code = ("import os, sys, time\n"
            "hold = lambda: [time.sleep(0.01) for _ in range(60000) if not os.path.exists(sys.argv[3])]\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"
            "    open(sys.argv[1] + '.tmp', 'w').write(str(os.getpid()))\n"
            "    os.rename(sys.argv[1] + '.tmp', sys.argv[1])\n"
            "    hold()\n"
            "    os._exit(0)\n"
            "while not os.path.exists(sys.argv[1]):\n"
            "    time.sleep(0.01)\n"
            "print('before', flush=True)\n"
            "open(sys.argv[2], 'w').write('x')\n"
            "hold()\n")
    finished, outcome = threading.Event(), {}

    def call():
        try:
            outcome["done"] = _py(code, str(pid_file), str(said), str(stop), timeout=600)
        except BaseException as exc:  # noqa: BLE001 - handed to the test below
            outcome["error"] = exc
        finally:
            finished.set()

    fired = _fire_timeout_once_written(monkeypatch, said)  # the timeout fires at once when the child says so
    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    try:
        wait_until(lambda: fired or finished.is_set(), 120, what="the child to escape and say so")
        wait_for_event(finished, 30, what="the call to return while the escaped child holds the pipes")
        done = outcome.get("done") or pytest.fail(f"the call raised {outcome.get('error')!r}")
        assert _alive(int(pid_file.read_text()))  # the escaped child is alive and holds stdout and stderr
        assert done.timed_out and done.stdout == "before\n"
    finally:
        stop.write_text("x")  # both processes leave, freeing the pipes a blocked call waits on, with or without a pid
        worker.join(30)
