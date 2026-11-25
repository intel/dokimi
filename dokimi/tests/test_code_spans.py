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
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import pytest

from dokimi import code_spans as code_spans_module
from dokimi.code_spans import LINE_BREAK, CodeSpan, CodeSpanScanner, _Runs, iter_code_spans, replace_code_spans
from dokimi.eval.degrade import _replace_code_blocks
from dokimi.external.online_checks import _BLOCK_START, _BLOCK_START_RE, _distinct_urls
from dokimi.external.skill_markdown import code_spans, link_pieces
from dokimi.memory.store import _blank_inline, _code_span, _escaped
from tests.growth_timing import FACTOR, GROWTH_LIMIT, growth


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
        new = CodeSpanScanner(text, pos, **site.options).find(pos)
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
    assert CodeSpanScanner("``a``", 1).find(1) is None
    assert CodeSpanScanner("``a`` `b`", 1).find(1).group(2) == "b"  # type: ignore[union-attr]
    assert [s.group(2) for s in code_spans("\\``a`` `b`")] == ["b"]  # the escaped run is skipped whole


def test_a_closer_is_a_run_of_the_opener_length_and_a_body_is_not_empty() -> None:
    assert list(iter_code_spans("`a``")) == []
    assert list(iter_code_spans("````")) == []
    assert [s.group(2) for s in iter_code_spans("`a``b``c`")] == ["a``b``c"]


def test_the_stop_ends_the_body_and_a_closer_may_start_on_it() -> None:
    assert list(iter_code_spans("`a\nb`", stop=LINE_BREAK)) == []
    assert [s.group(2) for s in iter_code_spans("`a`\n`b`", stop=LINE_BREAK)] == ["a", "b"]
    assert [s.group(2) for s in iter_code_spans("`a\nb`")] == ["a\nb"]


def test_a_span_has_three_groups() -> None:
    span = CodeSpanScanner("``a``").find(0)
    assert span is not None
    assert (span.group(), span.group(1), span.group(2)) == ("``a``", "``", "a")
    for index in (3, -1):
        with pytest.raises(IndexError):
            span.group(index)


def test_a_search_that_goes_back_finds_the_same_span_again() -> None:
    scanner = CodeSpanScanner("`a` ``b`` `c`")
    spans = [scanner.find(pos) for pos in (0, 4, 10, 4, 0, 11)]
    assert [s.group(2) for s in spans if s] == ["a", "b", "c", "b", "a"]
    assert spans[-1] is None


def test_sequential_spans_are_found_without_a_binary_search(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    bisect = code_spans_module.bisect_left

    def counting(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        return bisect(*args, **kwargs)

    monkeypatch.setattr(code_spans_module, "bisect_left", counting)
    text = "word `a` and ``b`` and `c` " * 200
    assert len(list(iter_code_spans(text))) == 600
    assert calls == 0


def _runs(k: int) -> str:
    """The issue's text: runs of 1, 2, ... k backticks, each followed by ``a``; no run has a closer."""
    return "".join("`" * i + "a" for i in range(1, k + 1))


def _runs_then_pairs(k: int) -> str:
    """Runs of 2 to k with no closer, then single backticks that pair up."""
    return _runs(k)[3:] + "`b" * (8 * k)


def _runs_between_pairs(k: int) -> str:
    """Runs of 2 to k with no closer, each followed by a pair of single backticks that does close."""
    return "".join("`" * i + "a `b` " for i in range(2, k + 2))


def _blanked_by_old_regex(line: str) -> str:
    """``line`` with the spans of the old regex blanked, searching again from each span's end."""
    pos = 0
    while (m := _old_store_span(line, pos)) is not None:
        line = line[:m.start()] + " " * (m.end() - m.start()) + line[m.end():]
        pos = m.end()
    return line


@pytest.mark.parametrize("seed", range(3))
def test_blanking_a_line_blanks_the_spans_of_the_old_regex(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(600):
        text = "".join(rng.choices("`a \\", [6, 3, 2, 1], k=rng.randint(4, 30)))
        assert _blank_inline(text, False)[0] == _blanked_by_old_regex(text), text
    assert _blank_inline(_runs_between_pairs(9), False)[0].strip("` a") == ""


def test_blanking_a_line_reads_each_run_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reads = 0
    read = _Runs.read

    def counting(self: _Runs) -> bool:
        nonlocal reads
        reads += 1
        return read(self)

    monkeypatch.setattr(_Runs, "read", counting)
    counts = []
    for k in (50, 200):
        reads = 0
        _blank_inline(_runs_between_pairs(k), False)
        counts.append(reads)
    assert counts[1] <= 8 * counts[0], counts  # 4x the runs, 4x the reads: a rescan per span would make it 16x


def _runs_on_two_lines(k: int) -> str:
    """Each run on the first line has its closer on the second."""
    return _runs(k) + "\n" + _runs(k)


def _production(name: str) -> Callable[[str], Any]:
    def store(text: str) -> list[tuple[int, int]]:
        out, pos, scan = [], 0, CodeSpanScanner(text, 0, stop=LINE_BREAK)
        while (m := _code_span(text, pos, scan)) is not None:
            out.append(m.span())
            pos = m.end()
        return out

    return {
        "inline_fence": lambda text: _replace_code_blocks(text, str.upper),
        "skill_markdown": lambda text: link_pieces([text]),
        "memory_store": store,
        "online_checks": _distinct_urls,
    }[name]


SHAPES = [_runs, _runs_then_pairs, _runs_on_two_lines, _runs_between_pairs]
# online_checks does a few whole-text passes (splitlines, join, replace) besides the span scan; the text of these
# shapes grows with k squared, so its time ratio sits near the limit and flakes: it is checked by reads below.
TIMED = [name for name in NAMES if name != "online_checks"]


# Test-narrowed: the online_checks cases of the timing test are counted in test_online_checks_reads_runs_in_linear_work
@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("name", TIMED)
def test_runs_with_no_closer_are_read_in_linear_time(name: str, shape: Callable[[int], str]) -> None:
    _same(name, shape(9))
    ratio, _ = growth(lambda k: partial(_production(name), shape(k)), 25)
    assert ratio < GROWTH_LIMIT


@pytest.mark.parametrize("shape", SHAPES)
def test_online_checks_reads_runs_in_linear_work(monkeypatch: pytest.MonkeyPatch, shape: Callable[[int], str]) -> None:
    """The URL scan reads each run a bounded number of times: 16x the runs is at most 32x the reads, 256x if a
    span rescanned them. Counted, not timed: the passes over the characters are not what this guards."""
    _same("online_checks", shape(9))
    reads = 0
    read = _Runs.read

    def counting(self: _Runs) -> bool:
        nonlocal reads
        reads += 1
        return read(self)

    monkeypatch.setattr(_Runs, "read", counting)
    counts = []
    for k in (25, 25 * FACTOR):
        reads = 0
        _distinct_urls(shape(k))
        counts.append(reads)
    assert 0 < counts[0] and counts[1] <= 2 * FACTOR * counts[0], counts


@pytest.mark.parametrize("shape", SHAPES)
def test_online_checks_touches_the_text_in_linear_work(shape: Callable[[int], str]) -> None:
    """The characters handed to str methods and the calls of regex methods grow with the text, not with the text
    times the runs: a ``count``, ``find`` or ``upper`` over the whole body for each run (16x the text's growth) or a
    ``re`` call for each run fails, on any machine. The scan's own passes over the characters are admitted (it makes
    about one). Not seen: slices, loops in Python, and what one regex call does inside."""

    def measure(text: str) -> tuple[int, int]:
        chars = calls = 0

        def watch(frame: Any, event: str, arg: Any) -> None:
            nonlocal chars, calls
            if event == "c_call":
                owner = getattr(arg, "__self__", None)
                if isinstance(owner, str):
                    chars += len(owner)
                elif isinstance(owner, re.Pattern):
                    calls += 1

        previous = sys.getprofile()
        sys.setprofile(watch)
        try:
            _distinct_urls(text)
        finally:
            sys.setprofile(previous)
        return chars, calls

    (small_chars, small_calls), (large_chars, large_calls) = (measure(shape(k)) for k in (25, 25 * FACTOR))
    text_growth = len(shape(25 * FACTOR)) / len(shape(25))
    assert 0 < small_chars and large_chars <= 4 * text_growth * small_chars, (small_chars, large_chars)
    assert large_calls <= 2 * small_calls, (small_calls, large_calls)
