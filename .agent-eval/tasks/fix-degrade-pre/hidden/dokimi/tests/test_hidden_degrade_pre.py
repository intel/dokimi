# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Hidden test for the fix-degrade-pre task: an HTML <pre> element is a code block too.
import pytest

from dokimi.eval.degrade import PADDING_PARAGRAPHS, hedge_concrete, padding, strip_code, wrong_numbers


def test_wrong_numbers_leaves_pre_contents_alone():
    answer = "It takes 5 ms.\n\n<pre>\nsleep 100 ms\n</pre>"
    assert wrong_numbers(answer) == answer.replace("5 ms", "50 ms", 1)


def test_padding_does_not_split_a_pre_block():
    out = padding("Code:\n\n<pre>\nline1\n\nline2\n</pre>\n\nDone.")
    assert "<pre>\nline1\n\nline2\n</pre>" in out, out
    assert all(p in out for p in PADDING_PARAGRAPHS)


class _FixedRng:
    """What hedge_concrete draws from: a fixed value, so the test is deterministic."""

    def random(self):
        return 0.5


@pytest.mark.parametrize("remove", [strip_code, lambda a: hedge_concrete(a, rng=_FixedRng())])
def test_code_removal_takes_the_whole_pre_block(remove):
    out = remove("Intro.\n\n<pre>\nmake all\n</pre>\n\nEnd.")
    assert "make all" not in out and "pre>" not in out, out
    assert "Intro." in out and "End." in out, out
