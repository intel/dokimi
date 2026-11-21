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

from dokimi.eval.reference_checks import _fenced_body, parse_json
from dokimi.novelty.symbols import _blank_skipped, header_names
from dokimi.repos.answers import _has_header_tail
from dokimi.repos.l0.core import _TRAILING_COMMENT, logical_lines
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
    (lambda k: "[P1]" * k, 375),  # the tags open and never close: quadratic as a lazy scan from each
    (lambda k: "a." * k + "x", 25),  # a path-like run with no line number: cubic as a search
    (lambda k: "a.:1 " * k + "x", 375),
], ids=["tags", "dots", "numbers"])
def test_header_tail_scan_is_linear(shape, n: int) -> None:  # type: ignore[no-untyped-def]
    ratio, found = growth(lambda k: partial(_has_header_tail, shape(k)), n)
    assert found is False
    assert ratio < GROWTH_LIMIT


# --- eval/reference_checks._FENCE ----------------------------------------------------------------

_OLD_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)

_FENCE_TABLE = [
    ("", None),
    ('{"a": 1}', None),
    ("```\nx\n```", "x"),
    ("```json\n{}\n```", "{}"),
    ("```JSON\n{}\n```", None),
    ("```json {}```", None),
    ("```\n\n```", ""),
    ("```\n\n\nx\n\n```", "x"),
    ("```\nx\n\n\n```\n\n", "x"),
    ("```\nx\n``` tail", None),
    ("```\nx```", None),
    ("x\n```\ny\n```", None),
    ("```\na\n```\nb\n```", "a\n```\nb"),
    ("```\na\n```\nb\n``` c", None),
    ("```\nx ``` y\n```", "x ``` y"),
    ("```\r\nx\r\n```\r\n", "x\r"),
    ("  ```json\n  x\n  ```  ", "  x"),
    ("```json\n\n```", ""),
    ("``````", None),
    ("```\n```", None),
]


@pytest.mark.parametrize(("text", "body"), _FENCE_TABLE)
def test_fence_table_matches_old_regex(text: str, body: str | None) -> None:
    old = _OLD_FENCE.match(text)
    assert (old.group(1) if old else None) == body
    assert _fenced_body(text) == body


def test_fence_random_texts_match_old_regex() -> None:
    pieces = ["```", "json", "\n", " ", "\t", "x", "{}"]
    texts = _random_texts(pieces, 926, longest=14)
    rng = random.Random(9260)
    blanks = ["", "\n", " ", "\n ", "\n\n", "\t\n"]  # the opener and the closer of a block, mostly well formed
    for _ in range(CASES):
        body = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 6)))
        texts.append(rng.choice(blanks) + "```" + rng.choice(["", "json"]) + rng.choice(blanks) + "\n" + body
                     + "\n" + rng.choice(blanks) + "```" + rng.choice(blanks + ["x", "```"]))
    old = [_OLD_FENCE.match(t) for t in texts]
    assert sum(1 for m in old if m) > CASES // 4
    assert sum(1 for m in old if m and m.group(1) == "") > 0
    assert [t for t, m in zip(texts, old, strict=True) if _fenced_body(t) != (m.group(1) if m else None)] == []


def test_parse_json_unwraps_a_fenced_block() -> None:
    assert parse_json('\n```json\n\n{"a": [1]}\n\n```\n') == (True, {"a": [1]})
    assert parse_json("```\n```") == (False, None)


@pytest.mark.parametrize("shape", [
    lambda k: "```\n" + "\n" * k,  # blank lines after an opener with no closer: cubic as the lazy body retried at each
    lambda k: "```\n" + "\n " * k,
    lambda k: "```\n" + "\n" * k + "x\n``` y",  # the closer is there but text follows it
], ids=["blank-lines", "blank-lines-spaces", "closer-then-text"])
def test_fence_scan_is_linear(shape) -> None:  # type: ignore[no-untyped-def]
    ratio, body = growth(lambda k: partial(_fenced_body, shape(k)), 50)
    assert body is None
    assert ratio < GROWTH_LIMIT


def test_fence_body_scan_is_linear_when_the_block_closes() -> None:
    ratio, body = growth(lambda k: partial(_fenced_body, "```\n" + "\n" * k + "x\n" + "\n" * k + "```"), 50)
    assert body == "x"
    assert ratio < GROWTH_LIMIT


# --- novelty/symbols._C_SKIP ---------------------------------------------------------------------

_OLD_C_SKIP = re.compile(
    r'/\*.*?\*/|//(?:\\\n|[^\n])*|"(?:\\.|[^"\\\n])*"|\'(?:\\(?:x[0-9A-Fa-f]+|[0-7]{1,3}|.)|[^\'\\\n])\'', re.DOTALL)

_C_TABLE = [
    ("", ""),
    ("int a;", "int a;"),
    ("a /* c */ b", "a   b"),
    ("a /**/ b", "a   b"),
    ("a /*/ b", "a /*/ b"),
    ("a /*/ b */ c", "a   c"),
    ("a /* open", "a /* open"),
    ("/* open /* open", "/* open /* open"),
    ('a "/* s */" b', "a   b"),
    ('a /* " */ b "s" c', "a   b   c"),
    ("a /* // */ b", "a   b"),
    ("a // /* x\nb */ c", "a  \nb */ c"),
    ("a // x \\\n y\nb", "a  \nb"),
    ("a // x \\\\\nb", "a  "),
    ("a '\\x41' b '\\077' c 'd' e", "a   b   c   e"),
    ("a 'ab' b", "a 'ab' b"),
    ('a "open', 'a "open'),
    ('a "open\nb "s" c', 'a "open\nb   c'),
    ('/* open "s" \'c\' // l\nx', "/* open      \nx"),
    ('x /* open "s" y', 'x /* open   y'),
    ("/* a */ /* open", "  /* open"),
    ("/* open */ */", "  */"),
    ("//*/ a /* b */", " "),
    ("/*/*/ x", "  x"),
    ("*/ /* x", "*/ /* x"),
]


@pytest.mark.parametrize(("text", "blanked"), _C_TABLE)
def test_c_skip_table_matches_old_regex(text: str, blanked: str) -> None:
    assert _OLD_C_SKIP.sub(" ", text) == blanked
    assert _blank_skipped(text) == blanked


def test_c_skip_random_texts_match_old_regex() -> None:
    texts = _random_texts(["/*", "*/", "//", '"', "'", "\\", "\n", "a", " ", "x41", "7"], 928, longest=16)
    blanked = [_OLD_C_SKIP.sub(" ", t) for t in texts]
    assert sum(1 for t, b in zip(texts, blanked, strict=True) if t != b) > CASES // 2
    assert sum(1 for t, b in zip(texts, blanked, strict=True) if t == b) > CASES // 20
    assert [t for t, b in zip(texts, blanked, strict=True) if _blank_skipped(t) != b] == []


def test_header_names_skips_comments_and_literals() -> None:
    text = '/* mkl_a /* mkl_b */ mkl_c "mkl_d" /* mkl_e\n// mkl_f\nint mkl_g;'
    assert header_names(text, ["mkl_"]) == {"mkl_c", "mkl_e", "mkl_g"}  # the unclosed `/*` hides nothing


@pytest.mark.parametrize("shape", [
    lambda k: "/* " * k,  # openers and no closer: each rescanned the rest of the text
    lambda k: "/*a" * k,
    lambda k: '/* "x" ' * k,
    lambda k: "/* a\n" * k,
    lambda k: "/* a */ " * k + "/* b " * k,  # closed comments, then openers with none
], ids=["openers", "adjacent", "strings", "lines", "closed-then-open"])
def test_c_skip_scan_is_linear(shape) -> None:  # type: ignore[no-untyped-def]
    ratio, blanked = growth(lambda k: partial(_blank_skipped, shape(k)), 375)
    assert blanked.count("/*") >= 6000
    assert ratio < GROWTH_LIMIT


def test_c_skip_scan_is_linear_when_the_closer_is_far() -> None:
    ratio, blanked = growth(lambda k: partial(_blank_skipped, "/* a " * k + "*/ b"), 375)
    assert blanked == "  b"
    assert ratio < GROWTH_LIMIT


# --- repos/l0/core.py: the trailing comment of a command line ------------------------------------

_OLD_COMMENT = r"\s+#.*$"

_COMMENT_TABLE = [
    ("", ""),
    ("cmd", "cmd"),
    ("cmd # c", "cmd"),
    ("cmd#c", "cmd#c"),
    ("cmd   # c  ", "cmd"),
    ("  # c", ""),
    ("cmd\t# c", "cmd"),
    ("a # b # c", "a"),
    ("#", "#"),
    (" #", ""),
    ("cmd # c\n", "cmd\n"),
    ("a\nb # c", "a\nb"),
    ("a # b\nc", "a # b\nc"),
    ("a # b\nc # d", "a # b\nc"),
    ("a\r\n# c\r\n", "a\n"),
    ("a\u00a0# c", "a"),
    ("x  \n  # c", "x"),
]


@pytest.mark.parametrize(("text", "stripped"), _COMMENT_TABLE)
def test_trailing_comment_table_matches_old_regex(text: str, stripped: str) -> None:
    assert re.sub(_OLD_COMMENT, "", text) == stripped
    assert _TRAILING_COMMENT.sub("", text) == stripped


def test_trailing_comment_random_texts_match_old_regex() -> None:
    texts = _random_texts([" ", "\t", "\n", "#", "x", "\\"], 931, longest=14)
    stripped = [re.sub(_OLD_COMMENT, "", t) for t in texts]
    assert sum(1 for t, s in zip(texts, stripped, strict=True) if t != s) > CASES // 4
    assert [t for t, s in zip(texts, stripped, strict=True) if _TRAILING_COMMENT.sub("", t) != s] == []


def test_logical_lines_joins_a_continuation_and_keeps_a_comment_after_a_backslash() -> None:
    assert logical_lines([(1, "a \\"), (2, "b # c")]) == [(1, "a  b")]
    assert logical_lines([(1, "a \\ # c"), (2, "# d")]) == [(1, "a \\ # c"), (2, "# d")]


@pytest.mark.parametrize("shape", [
    lambda k: "x" + " " * k,  # a run of whitespace and no `#`: every start inside the run took the rest of it
    lambda k: "x" + "\t" * k,
    lambda k: "x" + "\u00a0" * k,
], ids=["spaces", "tabs", "no-break-spaces"])
def test_trailing_comment_strip_is_linear(shape) -> None:  # type: ignore[no-untyped-def]
    ratio, stripped = growth(lambda k: partial(_TRAILING_COMMENT.sub, "", shape(k)), 375)
    assert stripped.startswith("x")
    assert ratio < GROWTH_LIMIT


def test_logical_lines_is_linear_on_a_long_whitespace_run() -> None:
    ratio, lines = growth(lambda k: partial(logical_lines, [(1, "x" + " " * k), (2, "y" + " " * k + "\\")]), 375)
    assert [n for n, _ in lines] == [1, 2]
    assert ratio < GROWTH_LIMIT
