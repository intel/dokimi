# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Spans from an opener to the first closer after it, in time linear in the text (#888).

``opener(.*?)closer`` tries every opener and scans to the end of the text for a closer that is
not there, so a text of n openers costs n squared steps. These helpers find the closers once, so an
opener with no closer after it costs no scan: no later opener of the same tag has one either, and
openers of other tags go on to their own closers.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable, Iterator
from dataclasses import dataclass


def pairs(text: str, opener: re.Pattern[str], closer: re.Pattern[str]) -> Iterator[tuple[re.Match[str], re.Match[str]]]:
    """``(opener, closer)`` matches of ``opener.*?closer``, as ``finditer`` would give them.

    The closer for an opener is the first one after it with the same group 1 (any closer when the
    patterns have no group). Closer matches must not overlap one another or an opener.
    """
    found: dict[str, list[re.Match[str]]] = {}
    for m in closer.finditer(text):
        found.setdefault(m.group(1) if m.re.groups else "", []).append(m)
    cursor: dict[str, int] = {}  # per key: the closers before this index start before the last opener's end
    pos = 0
    while (o := opener.search(text, pos)) is not None:
        key = o.group(1) if o.re.groups else ""
        closers = found.get(key, [])
        k = cursor.get(key, 0)
        if k and closers[k - 1].start() >= o.end():  # an opener that ends before the last one did
            k = bisect_left(closers, o.end(), 0, k, key=lambda c: c.start())
        while k < len(closers) and closers[k].start() < o.end():
            k += 1
        cursor[key] = k
        if k == len(closers):
            pos = o.start() + 1  # a key with no closer after this opener has none after a later one
            continue
        yield o, closers[k]
        pos = closers[k].end()


def replace_pairs(text: str, opener: re.Pattern[str], closer: re.Pattern[str], repl: Callable[[str], str]) -> str:
    """``text`` with each span :func:`pairs` finds, opener to closer, replaced by ``repl(span)``."""
    out, pos = [], 0
    for o, c in pairs(text, opener, closer):
        out += [text[pos:o.start()], repl(text[o.start():c.end()])]
        pos = c.end()
    return "".join(out) + text[pos:]


_QUOTE_MARKS = re.compile(r"[\\'\"\n]")


def _quote_after(text: str, at: int, end: int, quote: str) -> tuple[str, int]:
    """The quote (``'``, ``"`` or none) open at ``end`` on its line, scanning ``text`` from ``at`` where ``quote`` was open."""
    while (m := _QUOTE_MARKS.search(text, at, end)) is not None:
        mark, at = m.group(), m.end()
        if mark == "\n":
            quote = ""
        elif mark == "\\":
            at += quote != "'"
        elif not quote:
            quote = mark
        elif quote == mark:
            quote = ""
    return quote, max(at, end)


def _arithmetic_spans(text: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each ``$((...))`` in ``text``; an opener with no ``))`` after it ends the list."""
    spans, pos = [], 0
    while (start := text.find("$((", pos)) >= 0 and (close := text.find("))", start + 3)) >= 0:
        spans.append((start, close + 2))
        pos = close + 2
    return spans


@dataclass(frozen=True)
class HeredocPattern:
    """A heredoc: ``opener``, the rest of its line, a body, then a line holding only the opener's delimiter (``word`` is its group).

    ``full`` is the one regex of the whole heredoc and ``lines`` finds each line (group 1), which closes a heredoc as it is or without the whitespace around it.
    ``opener`` is the part of ``full`` up to the delimiter, without the rest of the line (an opener inside a quote of its
    line, ``grep '<<EOF' f``, or inside ``$((...))``, ``$((1<<3))``, is no opener, and a quoted delimiter is read up to its closing quote): the newline
    that ends the line is found once for the openers on it. The delimiter word is atomic in both
    regexes, so a line naming a prefix of it never closes the heredoc (#930). ``unclosed`` is the opener,
    the rest of its line and everything after it (same groups as ``full``): a heredoc no line closes
    runs to the end of the input, as in a shell. ``gap`` is the number of characters the closing line
    must start past the end of the opener's line (1 when ``full`` wants a body line first). ``before`` and ``after``
    are the characters ``full`` allows ahead of and after the delimiter on its closing line (all whitespace when None).
    """

    full: re.Pattern[str]
    opener: re.Pattern[str]
    lines: re.Pattern[str]
    unclosed: re.Pattern[str]
    word: int
    gap: int = 0
    before: str | None = None
    after: str | None = None

    def matches(self, text: str) -> Iterator[re.Match[str]]:
        """The matches of ``full``, then ``unclosed`` for the first opener that no line closes, which ends the scan.

        Only a line that is the opener's whole delimiter, starting ``gap`` past the end of the opener's line, closes it.
        A closing line right after the opener, where ``full`` wants a body line, leaves the opener uncut as it always was.
        The start of the last line of each delimiter is found once; a line that is no opener's delimiter is not kept.
        """
        openers = self._openers(text)
        if not openers:
            return
        last = dict.fromkeys((o.group(self.word) for o, _ in openers), -1)
        cores: dict[str, set[str]] = {}
        for delimiter in last:
            cores.setdefault(self._core(delimiter), set()).add(delimiter)
        sizes = {core: sorted({len(d) for d in delimiters}) for core, delimiters in cores.items()}
        for line in self.lines.finditer(text):
            core = self._core(line.group(1))
            for delimiter in self._closed(line.group(1), cores.get(core, set()), sizes.get(core, [])):
                last[delimiter] = line.start()
        pos = 0
        for o, line_end in openers:
            if o.start() < pos:
                continue
            closing = last[o.group(self.word)]
            if closing >= line_end + self.gap:
                m = self.full.match(text, o.start())
                assert m is not None  # a closing line past the opener is what ``full`` ends on
                yield m
                pos = m.end()
            elif closing < line_end:
                m = self.unclosed.match(text, o.start())
                assert m is not None  # the opener's line has a newline, so ``unclosed`` reaches the end
                yield m
                return
            else:
                pos = o.start() + 1

    def _core(self, line: str) -> str:
        """``line`` without the characters ``full`` allows before and after a delimiter."""
        return line.lstrip(self.before).rstrip(self.after)

    def _closed(self, line: str, delimiters: set[str], sizes: list[int]) -> Iterator[str]:
        """The ``delimiters`` (of the lengths ``sizes``, all of the core of ``line``) that ``full`` reads ``line`` as: an allowed run, the delimiter, an allowed run.

        A delimiter may start or end in such characters (``<<' a'``). Only the positions it can start at are read, a few per length
        (the blanks the delimiter owns), so the delimiters that share a core are not each tried against every line.
        """
        end = len(line.rstrip(self.after))
        lead = len(line) - len(line.lstrip(self.before))
        for size in sizes:
            for i in range(max(0, end - size), min(lead, len(line) - size) + 1):
                if line[i:i + size] in delimiters:
                    yield line[i:i + size]

    def _openers(self, text: str) -> list[tuple[re.Match[str], int]]:
        """Each opener with the start of the line after its own; an opener whose line has no end is left out."""
        openers, start, eol = [], 0, -1
        quote, scanned = "", 0
        arithmetic, next_span = _arithmetic_spans(text), 0
        while (o := self.opener.search(text, start)) is not None:
            quote, scanned = _quote_after(text, scanned, o.start(), quote)
            while next_span < len(arithmetic) and arithmetic[next_span][1] <= o.start():
                next_span += 1
            in_arithmetic = next_span < len(arithmetic) and arithmetic[next_span][0] <= o.start()
            escaped = scanned > o.start()  # a backslash before the first ``<`` made it a literal one: ``echo \<< f``
            if quote or in_arithmetic or escaped:  # ``grep '<<EOF' f``, ``$((1<<3))``: ``<<`` opens nothing
                start = o.start() + 1
                continue
            if o.end() > eol:  # delimiters end in increasing order, so one newline serves every opener before it
                eol = text.find("\n", o.end())
                if eol < 0:  # no opener from here on has a line end
                    break
            openers.append((o, eol + 1))
            start = o.start() + 1
        return openers

    def sub(self, repl: Callable[[re.Match[str]], str], text: str) -> str:
        """``full.sub(repl, text)``."""
        out, pos = [], 0
        for m in self.matches(text):
            out += [text[pos:m.start()], repl(m)]
            pos = m.end()
        return "".join(out) + text[pos:]
