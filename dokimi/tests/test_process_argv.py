# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An argv entry with a lone surrogate reaches the child as ``?``, a surrogateescape byte as itself (#859)."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from dokimi import process
from dokimi.process import Env, FakeRunner

PY = sys.executable
INHERIT = Env.inherit()
_PAIR = "a" + chr(0xD83D) + chr(0xDE00)  # what PyYAML and json load for a JSON-style escape of U+1F600
_POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="Windows encodes argv entries with surrogatepass")
_ARGV_BYTES = "import os, sys; sys.stdout.buffer.write(os.fsencode(sys.argv[1]))"


@_POSIX_ONLY
@pytest.mark.parametrize(("text", "sent"), [
    pytest.param("n\ud800", b"n?", id="d800-becomes-question-mark"),
    pytest.param("n\udcff", b"n\xff", id="dcff-stays-the-file-name-byte"),
    pytest.param("\udcff\ud800", b"\xff?", id="dcff-stays-beside-a-replaced-d800"),
    pytest.param(_PAIR, "a\U0001f600".encode(), id="pair-joined"),
])
def test_a_lone_surrogate_in_an_argv_entry_reaches_the_child_as_a_question_mark(text, sent):
    done = process.run([PY, "-c", _ARGV_BYTES, text], timeout=30, env=INHERIT)
    assert done.returncode == 0 and done.stdout_bytes == sent


def test_a_bytes_argv_entry_still_runs_as_given():
    done = process.run([PY, "-c", "import sys; print(sys.argv[1])", b"x"], timeout=30, env=INHERIT)  # type: ignore[list-item]
    assert done.returncode == 0 and done.stdout.strip() == "x"


class _BytesPath:
    def __fspath__(self) -> bytes:
        return b"x"


def test_a_path_like_entry_giving_bytes_runs_as_given():
    done = process.run([PY, "-c", "import sys; print(sys.argv[1])", _BytesPath()], timeout=30, env=INHERIT)  # type: ignore[list-item]
    assert done.returncode == 0 and done.stdout.strip() == "x"


@pytest.mark.parametrize("entry", [None, 1], ids=["none", "int"])
def test_an_argv_entry_that_is_not_text_bytes_or_a_path_is_refused_before_anything_opens(entry, tmp_path):
    out = tmp_path / "out.txt"
    out.write_text("kept")
    with pytest.raises(TypeError):
        process.run([PY, "-c", "pass", entry], timeout=30, env=INHERIT, stdout_to=out)
    with pytest.raises(TypeError):
        FakeRunner()(["x", entry], timeout=1, env=INHERIT)
    assert out.read_text() == "kept"


@_POSIX_ONLY
def test_completed_argv_holds_the_text_sent():
    done = process.run([PY, "-c", "pass", "n\ud800", "plain"], timeout=30, env=INHERIT)
    assert done.argv[-2:] == ("n?", "plain")


@_POSIX_ONLY
def test_fake_runner_records_an_argv_entry_as_sent():
    fake = FakeRunner()
    assert fake(["x", "n\ud800", "n\udcff", _PAIR], timeout=1, env=INHERIT).argv == ("x", "n?", "n\udcff", "a\U0001f600")
    assert fake.calls[0].argv == ("x", "n?", "n\udcff", "a\U0001f600")


@_POSIX_ONLY
def test_a_non_ascii_argv_entry_in_an_ascii_file_system_is_refused_or_sent_as_given():
    code = ("import sys\nfrom dokimi import process\nfrom dokimi.process import Env\n"
            "s = 'caf' + chr(0xe9)\n"
            "done = process.run([sys.executable, '-c', 'pass', s], timeout=30, env=Env.inherit())\n"
            "print('SENT' if done.argv[-1] == s else 'CHANGED')\n")
    env = {**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"}
    done = subprocess.run([PY, "-c", code], capture_output=True, text=True, env=env, timeout=120, check=False)
    assert (done.returncode != 0 and "UnicodeEncodeError" in done.stderr) or done.stdout.strip() == "SENT", done.stdout + done.stderr
