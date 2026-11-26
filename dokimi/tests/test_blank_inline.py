# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``_blank_inline`` blanks a line in one pass, and blanks what the version that rebuilt the line per span did (#1122)."""

from __future__ import annotations

import itertools
import random
import re
from functools import partial

import pytest

from dokimi.code_spans import LINE_BREAK, CodeSpanScanner
from dokimi.memory.store import _angle_link_end, _blank_inline, _code_span, _unpaired_run
from tests.growth_timing import GROWTH_LIMIT, growth


def _rebuilding(line: str, closes_ahead: bool, pos: int = 0, before: str = "") -> tuple[str, str, re.Match | None]:
    """The function as it was: the line is rebuilt, and every later read sees the blanked text."""
    scan = CodeSpanScanner(line, pos, stop=LINE_BREAK)
    while True:
        code = _code_span(line, pos, scan)
        start = line.find("<!--", pos)
        if code is None and start < 0:
            return line, "", _unpaired_run(line, before)
        if code is not None and (start < 0 or code.start() < start):
            if (link := _angle_link_end(line, code.start(), before)) is not None:
                pos = link
                continue
            line = line[:code.start()] + " " * (code.end() - code.start()) + line[code.end():]
            pos = code.end()
            continue
        end = line.find("-->", start + 2)
        if end >= 0:
            line = line[:start] + " " * (end + 3 - start) + line[end + 3:]
            pos = end + 3
            continue
        block = start <= 3 and not line[:start].strip()
        if block or closes_ahead:
            return line[:start] + " " * (len(line) - start), "block" if block else "inline", None
        pos = start + 4


def _same(line: str, closes_ahead: bool, pos: int = 0, before: str = "") -> None:
    new = _blank_inline(line, closes_ahead, pos, before)
    old = _rebuilding(line, closes_ahead, pos, before)
    assert (new[0], new[1], new[2] and new[2].span()) == (old[0], old[1], old[2] and old[2].span()), (line, closes_ahead, pos, before)


SHAPES = [
    "`a` b `c`",
    "x `a` <!-- c --> `b` <!-- d -->",
    "<!-- c --> `a`",
    "`a`<!-- open",
    "<!-- open `a` still",
    "x <!-- open `a` still",
    "`<!-- not a comment` -->",
    "<!-- `not a span -->` x",
    "[t](<`a`>) `b` [u](<x>)",
    "[t](<`a` `b`>) `c` and `d",
    "[t](`a`) `b` ](<`c`>) [d](<`e`>)",
    "`](` [t](<`a`>) `b`",
    "[a `](` b](<`c`>)",
    "``a`` `b` ```c`` `d",
    "\\`a` `b` \\`c\\`",
    "`a` `",
    "a ` b `` c ``` d",
    "<!--> `a` <!---> `b`",
]


@pytest.mark.parametrize("line", SHAPES)
@pytest.mark.parametrize("closes_ahead", [False, True])
@pytest.mark.parametrize("before", ["", "[open", "[x](`a`) [y", "a ](<b> ["])
def test_the_blanked_line_is_the_one_the_rebuilding_scan_left(line: str, closes_ahead: bool, before: str) -> None:
    _same(line, closes_ahead, 0, before)
    _same(line, closes_ahead, 3, before)


def test_the_blanked_line_matches_on_every_short_text_and_seeded_random_ones() -> None:
    for chars in itertools.product("`<!->[]( \\a", repeat=5):
        text = "".join(chars)
        _same(text, False)
        _same(text, True, 1, "[")
    rng = random.Random(1122)
    for _ in range(3000):
        text = "".join(rng.choice(["`", "``", "<!--", "-->", "[a](<", ">)", "](", " ", "\\", "x"]) for _ in range(12))
        _same(text, rng.random() < 0.5, rng.choice([0, 0, 2]), rng.choice(["", "[", "[a](b) ["]))


def _ticks(n: int) -> str:
    return "`a` " * n


def _comments(n: int) -> str:
    return "<!-- c --> " * n


def _mixed(n: int) -> str:
    return "`a` <!-- c --> " * n


def _unclosed(n: int) -> str:
    return "x <!-- c " * n


def _blanked_openers(n: int) -> str:
    return "`](` " * n


@pytest.mark.parametrize("shape", [_ticks, _comments, _mixed, _blanked_openers])
def test_blanking_a_line_of_many_spans_and_comments_grows_linearly(shape) -> None:
    ratio, result = growth(lambda k: partial(_blank_inline, shape(k), False), 2000)
    assert ratio < GROWTH_LIMIT
    assert result[0].strip() == ""


def test_scanning_past_many_comment_openers_with_no_close_grows_linearly() -> None:
    ratio, result = growth(lambda k: partial(_blank_inline, _unclosed(k), False), 2000)
    assert ratio < GROWTH_LIMIT
    assert result[0] == _unclosed(32000)
