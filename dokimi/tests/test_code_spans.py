# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Code spans are found in linear time, and the spans are those of the regexes they replaced (#1073).

The old regex of each site is the reference: the helper is compared with it on every text of a small
alphabet up to six characters, on seeded random texts, and on a table of the shapes the sites read.
"""

from __future__ import annotations

import itertools
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import pytest

from dokimi.code_spans import LINE_BREAK, CodeSpan, find_code_span, iter_code_spans, replace_code_spans
from dokimi.eval.degrade import _replace_code_blocks
from dokimi.external.online_checks import _BLOCK_START, _BLOCK_START_RE, _distinct_urls
from dokimi.external.skill_markdown import code_spans, link_pieces
from dokimi.memory.store import _code_span, _escaped
from tests.growth_timing import GROWTH_LIMIT, growth


@dataclass(frozen=True)
class Site:
    """One of the four sites: the regex it had, the helper's arguments that read the same spans, its alphabet."""

    old: re.Pattern[str]
    options: dict[str, Any]
    groups: int
    alphabet: str


SITES = {
    "inline_fence": Site(
        re.compile(r"(?<!`)(`{3,})(?!`)(.+?)(?<!`)\1(?!`)"), {"min_run": 3, "stop": LINE_BREAK}, 2, "`a \n\\"),
    "skill_markdown": Site(
        re.compile(r"(?<![\\`])(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL), {"not_after": "\\"}, 2, "`a \n\\"),
    "memory_store": Site(
        re.compile(r"(?<!`)(`+)(?!`)[^\n]*?(?<!`)\1(?!`)"), {"stop": LINE_BREAK}, 1, "`a \n\\"),
    "online_checks": Site(
        re.compile(r"(?<!`)(`+)(?!`)((?:(?!" + _BLOCK_START + r").)*?)(?<!`)\1(?!`)", re.DOTALL),
        {"stop": _BLOCK_START_RE}, 2, "`a \n-#"),
}
NAMES = list(SITES)


def _old(site: Site, m: re.Match[str]) -> tuple[Any, ...]:
    return (m.start(), m.end(), *(m.group(i) for i in range(site.groups + 1)))


def _new(site: Site, s: CodeSpan) -> tuple[Any, ...]:
    return (s.start(), s.end(), *(s.group(i) for i in range(site.groups + 1)))


def _same(name: str, text: str) -> None:
    site = SITES[name]
    want = [_old(site, m) for m in site.old.finditer(text)]
    assert [_new(site, s) for s in iter_code_spans(text, **site.options)] == want, repr(text)
    for pos in {0, 1, len(text) // 2, len(text)}:
        old = site.old.search(text, pos)
        new = find_code_span(text, pos, **site.options)
        assert (old and _old(site, old)) == (new and _new(site, new)), (text, pos)
    swapped = replace_code_spans(text, lambda s: "<" + s.group(1) + ">", **site.options)
    assert swapped == site.old.sub(lambda m: "<" + m.group(1) + ">", text), repr(text)


@pytest.mark.parametrize("name", NAMES)
def test_every_short_text_reads_as_the_old_regex_did(name: str) -> None:
    for chars in itertools.chain.from_iterable(itertools.product(SITES[name].alphabet, repeat=n) for n in range(7)):
        _same(name, "".join(chars))


@pytest.mark.parametrize("name", NAMES)
def test_random_texts_read_as_the_old_regex_did(name: str) -> None:
    rng = random.Random(1073)
    for _ in range(1500):
        weights = [6, 3, 2, 1, 1, 1][:len(SITES[name].alphabet)]
        _same(name, "".join(rng.choices(SITES[name].alphabet, weights, k=rng.randint(7, 40))))


_TABLE = [
    "`a`", "``a``", "```a```", "`` `a` ``", "`a``b`", "`a` `b`", "`a`b`c`", "``` ``` ```", "````", "`` ``",
    "`a\nb`", "``a\n\nb``", "\\`a`", "\\``a``", "a\\`b`c`", "`a\\`", "x `` y `", "`a``", "``a`", "```a``",
    "`a` ``b`` ```c```", "```x```y```z```", "`a`` b ``c`", "text\n\n- `a`\n- `b`", "`a\n- b`", "`a\n\nb`",
    "`a\n| b`", "`a\n> b`", "`a\n# b`", "`a\n---\nb`", "`a\n  \nb`", "`https://x.dev/a`", "```", "`", "",
]


@pytest.mark.parametrize("name", NAMES)
def test_the_shapes_the_sites_read_match_the_old_regex(name: str) -> None:
    for text in _TABLE:
        _same(name, text)
    for opener, closer in itertools.product(range(1, 6), repeat=2):
        for gap in ("x", " x ", "x\ny", ""):
            _same(name, "`" * opener + gap + "`" * closer)
            _same(name, "\\" + "`" * opener + gap + "`" * closer + "`" * opener + gap + "`" * opener)


def _store_spans(line: str, find: Callable[[str, int], Any]) -> list[tuple[int, int]]:
    out, pos = [], 0
    while (m := find(line, pos)) is not None:
        out.append((m.start(), m.end()))
        pos = m.end()
    return out


def _old_store_span(line: str, pos: int) -> re.Match[str] | None:
    while (m := SITES["memory_store"].old.search(line, pos)) is not None:
        if not _escaped(line, m.start()):
            return m
        pos = m.start() + 1
    return None


@pytest.mark.parametrize("seed", range(3))
def test_the_sites_read_the_spans_of_their_old_regex(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(600):
        text = "".join(rng.choices("`a \\", [6, 3, 2, 1], k=rng.randint(4, 30)))
        fence = SITES["inline_fence"].old.sub(lambda m: "<" + m.group() + ">", "x " + text)
        assert _replace_code_blocks("x " + text, lambda s: "<" + s + ">") == fence, text
        for lines in (text, text.replace("a", "\n")):
            assert _store_spans(lines, _code_span) == _store_spans(lines, _old_store_span), lines
        markdown = [(m.start(), m.end(), m.group(2)) for m in SITES["skill_markdown"].old.finditer(text)]
        assert [(m.start(), m.end(), m.group(2)) for m in code_spans(text)] == markdown, text
        want = {m.group(2).replace("\n", " ").strip(): None for m in SITES["skill_markdown"].old.finditer("x " + text)}
        assert link_pieces(["x " + text])[1] == [s for s in want if s], text


@pytest.mark.parametrize(("body", "urls"), [
    ("`https://a.dev/x`", []),
    ("`a\n\nhttps://b.dev/y`", ["https://b.dev/y"]),
    ("`a\n- https://c.dev/z`", ["https://c.dev/z"]),
    ("`a\nhttps://d.dev/w`", []),
    ("``a ` https://e.dev/v ``", []),
])
def test_a_url_in_a_code_span_stops_at_a_block_start(body: str, urls: list[str]) -> None:
    assert _distinct_urls(body)[0] == urls


def test_a_run_cut_by_the_start_position_opens_no_span() -> None:
    assert find_code_span("``a``", 1) is None
    assert find_code_span("``a`` `b`", 1).group(2) == "b"  # type: ignore[union-attr]
    assert [s.group(2) for s in code_spans("\\``a`` `b`")] == ["b"]  # the escaped run is skipped whole


def test_a_closer_is_a_run_of_the_opener_length_and_a_body_is_not_empty() -> None:
    assert list(iter_code_spans("`a``")) == []
    assert list(iter_code_spans("````")) == []
    assert [s.group(2) for s in iter_code_spans("`a``b``c`")] == ["a``b``c"]


def test_the_stop_ends_the_body_and_a_closer_may_start_on_it() -> None:
    assert list(iter_code_spans("`a\nb`", stop=LINE_BREAK)) == []
    assert [s.group(2) for s in iter_code_spans("`a`\n`b`", stop=LINE_BREAK)] == ["a", "b"]
    assert [s.group(2) for s in iter_code_spans("`a\nb`")] == ["a\nb"]


def _runs(k: int) -> str:
    """The issue's text: runs of 1, 2, ... k backticks, each followed by ``a``; no run has a closer."""
    return "".join("`" * i + "a" for i in range(1, k + 1))


def _runs_then_pairs(k: int) -> str:
    """Runs of 2 to k with no closer, then single backticks that pair up."""
    return _runs(k)[3:] + "`b" * (8 * k)


def _runs_on_two_lines(k: int) -> str:
    """Each run on the first line has its closer on the second."""
    return _runs(k) + "\n" + _runs(k)


def _production(name: str) -> Callable[[str], Any]:
    def store(text: str) -> list[tuple[int, int]]:
        out, pos = [], 0
        while (m := _code_span(text, pos)) is not None:
            out.append(m.span())
            pos = m.end()
        return out

    return {
        "inline_fence": lambda text: _replace_code_blocks(text, str.upper),
        "skill_markdown": lambda text: link_pieces([text]),
        "memory_store": store,
        "online_checks": _distinct_urls,
    }[name]


@pytest.mark.parametrize("shape", [_runs, _runs_then_pairs, _runs_on_two_lines])
@pytest.mark.parametrize("name", NAMES)
def test_runs_with_no_closer_are_read_in_linear_time(name: str, shape: Callable[[int], str]) -> None:
    _same(name, shape(9))
    ratio, _ = growth(lambda k: partial(_production(name), shape(k)), 25)
    assert ratio < GROWTH_LIMIT
