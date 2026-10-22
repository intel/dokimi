"""Progress lines of `repo tasks check` / `repo run` are flushed one by one, not at exit."""

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
        assert proc.stdout.readline() == "t1: OK\n"  # would block 30 s if buffered
    finally:
        proc.kill()
        proc.wait()


def test_commands_hand_the_flushing_logger_to_the_runners():
    import inspect

    from dokimi.commands import repo_run

    src = inspect.getsource(repo_run)
    assert src.count("log=_progress") == 2
