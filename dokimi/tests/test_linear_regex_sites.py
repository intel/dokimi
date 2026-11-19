# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Regex sites that took superlinear time on one long line now scan in linear time, with the old result.

Each section keeps the old regex as the reference and compares the new code with it on a hand table and
on seeded random strings built from the pieces of the site's grammar (https://github.com/intel/dokimi/issues/888).
"""

from __future__ import annotations

import random
import re
from functools import partial

import pytest

from dokimi.repos.answers import _has_header_tail
from tests.growth_timing import GROWTH_LIMIT, growth

CASES = 3000


def _random_texts(pieces: list[str], seed: int, longest: int = 12) -> list[str]:
    rng = random.Random(seed)
    return ["".join(rng.choice(pieces) for _ in range(rng.randint(0, longest))) for _ in range(CASES)]


# --- repos/answers._HEADER_TAIL ------------------------------------------------------------------

_OLD_HEADER_TAIL = re.compile(r"\[P\d\].*?(?:—| [-] )|[\w./-]*[./][\w./-]*:\d+(?:-\d+)?\W*[ \t\r]*$", re.I)

_HEADER_TAIL_TABLE = [
    ("no tag here", False),
    ("", False),
    ("- [P1] title — src/a.py:3", True),
    ("- [P1] title - note", True),
    ("- [P1] title-note", False),
    ("- [p1] title — x", True),
    ("- [P12] title — x", False),
    ("[P1] title", False),
    ("[P1] [P2] — x", True),
    ("[P1] a\nb — c", False),
    ("[P1] a\n[P2] b — c", True),
    ("see src/a.py:12", True),
    ("see src/a.py:12-14", True),
    ("see src/a.py:12   ", True),
    ("see src/a.py:12 \t\r", True),
    ("see src/a.py:12\n", True),
    ("see src/a.py:12.", True),
    ("see src/a.py:12-", True),
    ("see src/a.py:12-x", False),
    ("see src/a.py:12 and more", False),
    ("a.b:3", True),
    ("a:3", False),
    (":3", False),
    ("meet at 12:30", False),
    ("src/a.py: 12", False),
    ("src/a.py:12:30", False),
    ("x.py:1 y.py:2", True),
    ("x.py:1 y:2", False),
]


@pytest.mark.parametrize(("text", "expected"), _HEADER_TAIL_TABLE)
def test_header_tail_table_matches_old_regex(text: str, expected: bool) -> None:
    assert bool(_OLD_HEADER_TAIL.search(text)) is expected
    assert _has_header_tail(text) is expected


def test_header_tail_random_texts_match_old_regex() -> None:
    pieces = ["[P1]", "[p2]", "—", " - ", "a", ".", "/", "-", ":", "1", "23", " ", "\t", "\r", "\n"]
    texts = _random_texts(pieces, 925)
    assert any(_OLD_HEADER_TAIL.search(t) for t in texts)
    assert not all(_OLD_HEADER_TAIL.search(t) for t in texts)
    assert [t for t in texts if _has_header_tail(t) != bool(_OLD_HEADER_TAIL.search(t))] == []


@pytest.mark.parametrize(("shape", "n"), [
    (lambda k: "[P1]" * k, 1500),  # the tags open and never close: quadratic as a lazy scan from each
    (lambda k: "a." * k + "x", 100),  # a path-like run with no line number: cubic as a search
    (lambda k: "a.:1 " * k + "x", 1500),
], ids=["tags", "dots", "numbers"])
def test_header_tail_scan_is_linear(shape, n: int) -> None:  # type: ignore[no-untyped-def]
    ratio, found = growth(lambda k: partial(_has_header_tail, shape(k)), n)
    assert found is False
    assert ratio < GROWTH_LIMIT
