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


_QUOTE_MARKS = re.compile(r"[\\'\"\n#]")


def _quote_step(text: str, m: re.Match[str], quote: str) -> tuple[str, int]:
    """The quote open after the mark ``m`` (one of ``_QUOTE_MARKS``) where ``quote`` was open, and where the scan goes on."""
    mark, at = m.group(), m.end()
    if mark == "\n":
        return "", at
    if quote == "#":
        return quote, at
    if mark == "#":  # `a#b` and `$#` start no comment
        return ("#" if not quote and (m.start() == 0 or text[m.start() - 1].isspace()) else quote), at
    if mark == "\\":
        return quote, at + (quote != "'")
    if not quote:
        return mark, at
    return ("" if quote == mark else quote), at


def _quote_after(text: str, at: int, end: int, quote: str) -> tuple[str, int]:
    """The quote (``'``, ``"``, ``#`` for a comment, which runs to the end of its line, or none) open at ``end``, scanning ``text`` from ``at`` where ``quote`` was open."""
    while (m := _QUOTE_MARKS.search(text, at, end)) is not None:
        quote, at = _quote_step(text, m, quote)
    return quote, max(at, end)


_ARITHMETIC_MARKS = re.compile(r"[\\'\"\n#()$]")


def _arithmetic_spans(text: str) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each ``$((...))`` in ``text``, closed by the parenthesis that balances its two; an opener with none ends the list.

    A ``$((`` inside single quotes or a comment opens nothing, as a backslash before the ``$`` does not either.
    """
    spans, quote, depth, start, pos = [], "", 0, 0, 0
    while (m := _ARITHMETIC_MARKS.search(text, pos)) is not None:
        mark, pos = m.group(), m.end()
        if depth:
            if mark == "\\":
                pos += 1
            elif mark in "()":
                depth += 1 if mark == "(" else -1
                if not depth:
                    spans.append((start, pos))
        elif mark == "$":
            if quote not in ("'", "#") and text.startswith("((", pos):
                start, depth, pos = m.start(), 2, pos + 2
        elif mark not in "()":
            quote, pos = _quote_step(text, m, quote)
    return spans


@dataclass(frozen=True)
class HeredocPattern:
    """A heredoc: ``opener``, the rest of its line, a body, then a line holding only the opener's delimiter (``word`` is its group).

    ``full`` is the one regex of the whole heredoc and ``lines`` finds each line (group 1), which closes a heredoc as it is or without the whitespace around it.
    ``opener`` is the part of ``full`` up to the delimiter, without the rest of the line (an opener inside a quote of its
    line, ``grep '<<EOF' f``, or inside ``$((...))``, ``$((1<<3))``, or a ``#`` comment, ``# usage: cat <<EOF``, is no opener, and a quoted delimiter is read up to its closing quote): the newline
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
        cores: dict[str, dict[int, set[str]]] = {}
        for delimiter in last:
            cores.setdefault(self._core(delimiter), {}).setdefault(len(delimiter), set()).add(delimiter)
        for line in self.lines.finditer(text):
            for delimiter in self._closed(line.group(1), cores.get(self._core(line.group(1)), {})):
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

    def _closed(self, line: str, by_size: dict[int, set[str]]) -> Iterator[str]:
        """The delimiters (``by_size``: those of the core of ``line``, by length) that ``full`` reads ``line`` as: an allowed run, the delimiter, an allowed run.

        A delimiter may start or end in such characters (``<<' a'``). Only the positions it can start at are read, and for a length
        with fewer delimiters than positions, each delimiter is searched for in them at once, so neither the delimiters that share a core
        nor the lengths of a line of blanks are each tried against every position.
        """
        end = len(line.rstrip(self.after))
        lead = len(line) - len(line.lstrip(self.before))
        for size, delimiters in by_size.items():
            first, last = max(0, end - size), min(lead, len(line) - size)
            if len(delimiters) < last - first + 1:
                yield from (d for d in delimiters if line.find(d, first, last + size) >= 0)
                continue
            for i in range(first, last + 1):
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
