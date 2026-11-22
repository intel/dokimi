# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The loose citation scan took quadratic time on one long token and now takes one pass, with the old result.

Each section keeps the old regex as the reference and compares the new code with it on a hand table and
on seeded random texts from a small alphabet (https://github.com/intel/dokimi/issues/924).
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from typing import Any

import pytest

from dokimi.metrics import exploration
from tests.growth_timing import GROWTH_LIMIT, growth

CASES = 600


def _random_texts(pieces: list[str], seed: int, longest: int = 14) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(0, longest))) for _ in range(CASES)]


def _linear(call: Callable[[str], Any], shape: Callable[[int], str], n: int = 2_000) -> None:
    ratio, _ = growth(lambda k: partial(call, shape(k)), n)
    assert ratio < GROWTH_LIMIT


# --- exploration._parse_loose (#924) -------------------------------------------------------------

_OLD_LOOSE_CITATION = re.compile(
    r"(?P<open>[(\[{<\"'])?"
    r"(?P<path>[^\s:`]+\.[A-Za-z0-9_+]+)`?"
    r":(?P<start>\d+)(?:\s*[-–:]\s*(?P<end>\d+))?"
    r"(?P<close>[)\]}>\"'])?"
)


def _old_parse_loose(text: str) -> exploration.CitationSet:
    citations = []
    for m in _OLD_LOOSE_CITATION.finditer(text):
        opener = m.group("open")
        cite = exploration._citation_from_match(m)
        if opener in exploration._WRAPPERS and exploration._WRAPPERS[opener] in m.group("path"):
            cite = replace(cite, path=exploration.normalize_path(opener + m.group("path")))
        citations.append(cite)
    return exploration._finish(citations, [])


_WRAPPED = [f"{o}src/a.py:10-20{c}" for o, c in zip("([{<\"'", ")]}>\"'", strict=True)]

LOOSE_TABLE = [
    "",
    "no citation here",
    "see src/a.py:10",
    "see src/a.py:10-20 and src/b.py:3",
    "see src/a.py:10–20",
    "see src/a.py:10:20",
    "see src/a.py:10 - 20",
    "see src/a.py:10 -",
    "see `src/a.py`:42-58 near",
    "`src/a.py`:7",
    "src/a.py`:7",
    "x.py:1:2",
    "x.py:1:2:3",
    "x.py:1 y.py:2",
    "x.py:1y.py:2",
    "a.b.c.d:5",
    "a.b.c.:5",
    ".py:5",
    "a.:5",
    "a.b:",
    "a.b:x",
    "src/a:5",
    "noext:5",
    ":5",
    "a b.py:5",
    "(app)/page.tsx:10",
    "([id].tsx:5) and (app)/x.tsx:3.",
    "((a.py:1))",
    "(.py:1)",
    "(a.py:1",
    "a.py:1)",
    "a.py:1)b.py:2",
    "(a.py:1)(b.py:2)\"c.py:3\"d.py:4",
    "<a.py:1>",
    "\"a.py:1\" 'b.py:2'",
    "a٣.py:٣",
    "x+y_z.c++:12",
    "a.b-c:3",
    "a.py:1\nb.py:2\n",
    *_WRAPPED,
    *(f"see {w} here" for w in _WRAPPED),
]


@pytest.mark.parametrize("text", LOOSE_TABLE)
def test_loose_citations_read_as_the_old_regex_did(text: str) -> None:
    assert exploration.parse_final_answer(text) == _old_parse_loose(text)


def test_loose_citations_match_the_old_regex_on_random_text() -> None:
    texts = _random_texts(["a", ".", ":", "1", "`", "(", ")", " ", "-", "\n", "a.a", ":1"], 924, longest=16)
    assert len({_old_parse_loose(t) for t in texts}) > 20
    for text in texts:
        assert exploration.parse_final_answer(text) == _old_parse_loose(text), repr(text)


def test_loose_citations_match_the_old_regex_on_random_citation_pieces() -> None:
    pieces = ["src/a.py", ":3", "-4", "–5", ":6", "`", "(", ")", "[", "]", "\"", " ", "x.", "\n", "ab"]
    for text in _random_texts(pieces, 925, longest=10):
        assert exploration.parse_final_answer(text) == _old_parse_loose(text), repr(text)


def test_a_long_path_is_still_a_citation() -> None:
    path = "d/" * 5000 + "a.py"
    assert exploration.parse_final_answer(f"{path}:7").citations[0].path == path


def test_a_long_token_without_a_citation_is_scanned_in_linear_time() -> None:
    _linear(exploration.parse_final_answer, lambda k: "a" * k)


def test_unclosed_final_answer_openers_fall_back_in_linear_time() -> None:
    _linear(exploration.parse_final_answer, lambda k: "<final_answer>" * k)
