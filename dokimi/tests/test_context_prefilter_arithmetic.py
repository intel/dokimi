# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Arithmetic expansion in the prefilter's heredoc scan: the same spans as the lazy regex, in linear time (#887)."""

from __future__ import annotations

import re

import pytest

from dokimi.context.prefilter import _blank_arithmetic, _without_heredoc_bodies
from tests.growth_timing import GROWTH_LIMIT, growth

_OLD = re.compile(r"\$\(\([\s\S]*?\)\)")


def _old_blank(text):
    return _OLD.sub(lambda m: "_" * len(m.group(0)), text)


@pytest.mark.parametrize("text", [
    "echo $((1<<2))",
    "$(( $((1)) ))",          # nested: the first ``))`` closes the outer span
    "$(())",                   # the closer follows the opener at once
    "$((1\n2))",               # a span over a newline
    "$(( $((1 + $((2))",       # unclosed openers
    "$((1 $((2))",             # an unclosed opener followed by a closed one
    "a)) $((1)) b",            # ``))`` before any opener
    "$(($(($((1))))))",
    "no arithmetic here",
    "",
])
def test_blank_arithmetic_gives_the_lazy_regexs_result(text):
    assert _blank_arithmetic(text) == _old_blank(text)


def test_a_shift_in_arithmetic_opens_no_heredoc_and_a_real_one_after_it_does():
    kept = _without_heredoc_bodies("echo $((1<<2)) <<EOF\nbody\nEOF\nmake ci-local")
    assert "body" not in kept and "make ci-local" in kept


def test_an_unclosed_run_of_openers_is_scanned_in_linear_time():
    def timed(n):
        command = "<<" + "$((" * n
        return lambda: _without_heredoc_bodies(command)

    ratio, result = growth(timed, 1500)
    assert result.startswith("<<")
    assert ratio < GROWTH_LIMIT
