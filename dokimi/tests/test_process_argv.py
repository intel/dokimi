# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An argv entry with a lone surrogate reaches the child as ``?``, a surrogateescape byte as itself (#859)."""

from __future__ import annotations

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


@_POSIX_ONLY
def test_completed_argv_holds_the_text_sent():
    done = process.run([PY, "-c", "pass", "n\ud800", "plain"], timeout=30, env=INHERIT)
    assert done.argv[-2:] == ("n?", "plain")


@_POSIX_ONLY
def test_fake_runner_records_an_argv_entry_as_sent():
    fake = FakeRunner()
    assert fake(["x", "n\ud800", "n\udcff", _PAIR], timeout=1, env=INHERIT).argv == ("x", "n?", "n\udcff", "a\U0001f600")
    assert fake.calls[0].argv == ("x", "n?", "n\udcff", "a\U0001f600")
