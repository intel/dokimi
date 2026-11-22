# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Markdown code spans, found in time linear in the text (#1073).

A code span is a maximal run of backticks, a body of one character or more, and the next maximal run of
the same length. ``(`+)(?!`)(.+?)(?<!`)\\1(?!`)`` scans to the end of the text for each run with no such
closer, and a text of runs of 1, 2, 3, ... backticks costs n to the power 1.5 steps. These helpers read
each run once and keep the scanned runs by length, so a run with no closer after it costs no second scan.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

_RUN = re.compile(r"`+")
LINE_BREAK = re.compile(r"\n")  # the ``stop`` of a code span whose body stays on one line


@dataclass(frozen=True)
class CodeSpan:
    """A code span, read as a ``re.Match`` of the old regexes was: group 1 the backtick run, group 2 the body."""

    text: str
    begin: int
    body_begin: int
    body_end: int
    finish: int

    def start(self) -> int:
        return self.begin

    def end(self) -> int:
        return self.finish

    def span(self) -> tuple[int, int]:
        return self.begin, self.finish

    def group(self, index: int = 0) -> str:
        if index == 0:
            return self.text[self.begin:self.finish]
        if index == 1:
            return self.text[self.begin:self.body_begin]
        if index == 2:
            return self.text[self.body_begin:self.body_end]
        raise IndexError("no such group")


class _Runs:
    """The runs of backticks of a text from a position, read left to right once and kept by length."""

    def __init__(self, text: str, pos: int, stop: re.Pattern[str] | None) -> None:
        self.text, self.pos, self.stop = text, pos, stop
        self._found = _RUN.finditer(text, pos)
        self.starts: list[int] = []
        self.lens: list[int] = []
        self._by_len: dict[int, list[int]] = {}  # length -> indexes of the runs read so far
        self._head: dict[int, int] = {}  # length -> the first index of its list not before the last opener
        self._edge = (len(text) + 1, 0)  # (a position, the first stop at or after it): a query between them reuses it

    def read(self) -> bool:
        """Read the next run; False when there is none."""
        m = next(self._found, None)
        if m is None:
            return False
        if m.start() == self.pos and self.pos > 0 and self.text[self.pos - 1] == "`":
            return True  # the tail of a run that began before ``pos``: no opener, and no closer after one
        self.starts.append(m.start())
        self.lens.append(m.end() - m.start())
        self._by_len.setdefault(self.lens[-1], []).append(len(self.starts) - 1)
        return True

    def limit(self, at: int) -> int:
        """The first position at or after ``at`` where the stop pattern matches; a closer may start there at the latest."""
        if self.stop is None:
            return len(self.text)
        if self._edge[0] > at or self._edge[1] < at:
            found = self.stop.search(self.text, at)
            self._edge = (at, found.start() if found else len(self.text))
        return self._edge[1]

    def closer(self, n: int, body: int) -> int | None:
        """The start of the first run of ``n`` backticks from ``body`` on, if it starts at or before the stop."""
        limit, same = self.limit(body), self._by_len[n]
        h = self._head.get(n, 0)
        while True:
            while h < len(same) and self.starts[same[h]] < body:
                h += 1
            self._head[n] = h
            if h < len(same):
                return self.starts[same[h]] if self.starts[same[h]] <= limit else None
            if self.starts[-1] > limit or not self.read():
                return None  # past the stop no later run can close it, and the text may end


def iter_code_spans(
    text: str, pos: int = 0, *, min_run: int = 1, stop: re.Pattern[str] | None = None, not_after: str = "",
) -> Iterator[CodeSpan]:
    """The code spans of ``text`` from ``pos`` on, left to right, each from the first opener that has a closer.

    ``min_run`` is the shortest opening run, ``not_after`` the characters a run may not follow (a backtick
    always), and ``stop`` a pattern whose match ends the body: a closer must start at or before its start.
    """
    runs = _Runs(text, pos, stop)
    i = after = 0
    while True:
        while i >= len(runs.starts):
            if not runs.read():
                return
        begin, n = runs.starts[i], runs.lens[i]
        i += 1
        if begin < after or n < min_run or (not_after and begin > 0 and text[begin - 1] in not_after):
            continue
        closer = runs.closer(n, begin + n)
        if closer is not None:
            after = closer + n
            yield CodeSpan(text, begin, begin + n, closer, after)


def find_code_span(
    text: str, pos: int = 0, *, min_run: int = 1, stop: re.Pattern[str] | None = None, not_after: str = "",
) -> CodeSpan | None:
    """The first code span of ``text`` from ``pos`` (see :func:`iter_code_spans`), like ``pattern.search(text, pos)``."""
    return next(iter_code_spans(text, pos, min_run=min_run, stop=stop, not_after=not_after), None)


def replace_code_spans(
    text: str, repl: Callable[[CodeSpan], str], *, min_run: int = 1, stop: re.Pattern[str] | None = None,
    not_after: str = "",
) -> str:
    """``text`` with each code span replaced by ``repl(span)``, like ``pattern.sub``."""
    out: list[str] = []
    last = 0
    for span in iter_code_spans(text, min_run=min_run, stop=stop, not_after=not_after):
        out.append(text[last:span.start()])
        out.append(repl(span))
        last = span.end()
    out.append(text[last:])
    return "".join(out)
