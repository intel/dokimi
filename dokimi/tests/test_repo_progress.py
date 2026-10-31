# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Progress lines of `eval check` / `repo run` are flushed one by one, not at exit."""

import select
import subprocess
import sys


def test_progress_line_is_flushed_under_a_pipe():
    code = ("import sys, time\n"
            "from dokimi.commands.repo_run import _progress\n"
            "_progress('t1: OK')\n"
            "time.sleep(30)\n")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                            env={"PATH": "", "PYTHONPATH": ":".join(sys.path)})
    try:
        assert proc.stdout is not None
        # A flushed line arrives at once; a buffered one stays in the child until it exits (30 s).
        ready, _, _ = select.select([proc.stdout], [], [], 5)
        assert ready, "no progress line within 5 s: the line was not flushed"
        assert proc.stdout.readline() == "t1: OK\n"
    finally:
        proc.kill()
        proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def test_commands_hand_the_flushing_logger_to_the_runners():
    import inspect

    from dokimi.commands import repo_run

    src = inspect.getsource(repo_run)
    assert src.count("log=_progress") == 2
