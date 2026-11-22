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


@dataclass(frozen=True)
class HeredocPattern:
    """A heredoc: ``opener``, the rest of its line, a body, then a line holding only the opener's delimiter (``word`` is its group).

    ``full`` is the one regex of the whole heredoc and ``lines`` finds a line of one word (group 1).
    ``opener`` is the part of ``full`` up to the delimiter, without the rest of the line: the newline
    that ends the line is found once for the openers on it. The delimiter word is atomic in both
    regexes, so a line naming a prefix of it never closes the heredoc (#930). ``gap`` is the number
    of characters the closing line must start past the end of the opener's line (1 when ``full``
    wants a body line first).
    """

    full: re.Pattern[str]
    opener: re.Pattern[str]
    lines: re.Pattern[str]
    word: int
    gap: int = 0

    def matches(self, text: str) -> Iterator[re.Match[str]]:
        """The matches of ``full``, but an opener that no line closes is skipped, not rescanned.

        Only a line that is the opener's whole delimiter, starting ``gap`` past the end of the opener's line, closes it.
        The start of the last line of each delimiter is found once; a line that is no opener's delimiter is not kept.
        """
        openers, start, eol = [], 0, -1
        while (o := self.opener.search(text, start)) is not None:
            if o.end() > eol:  # delimiters end in increasing order, so one newline serves every opener before it
                eol = text.find("\n", o.end())
                if eol < 0:  # no opener from here on has a line end
                    break
            openers.append((o, eol + 1))
            start = o.start() + 1
        if not openers:
            return
        last = dict.fromkeys((o.group(self.word) for o, _ in openers), -1)
        for line in self.lines.finditer(text):
            if line.group(1) in last:
                last[line.group(1)] = line.start()
        pos = 0
        for o, line_end in openers:
            if o.start() < pos:
                continue
            if last[o.group(self.word)] >= line_end + self.gap:
                m = self.full.match(text, o.start())
                assert m is not None  # a closing line past the opener is what ``full`` ends on
                yield m
                pos = m.end()
            else:
                pos = o.start() + 1

    def sub(self, repl: Callable[[re.Match[str]], str], text: str) -> str:
        """``full.sub(repl, text)``."""
        out, pos = [], 0
        for m in self.matches(text):
            out += [text[pos:m.start()], repl(m)]
            pos = m.end()
        return "".join(out) + text[pos:]
