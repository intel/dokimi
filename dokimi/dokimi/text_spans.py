# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Spans from an opener to the first closer after it, in time linear in the text (#888).

``opener(.*?)closer`` tries every opener and scans to the end of the text for a closer that is
not there, so a text of n openers costs n squared steps. These helpers find the closers once and
stop at the first opener that has none: no later opener has one either.
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
    pos = 0
    while (o := opener.search(text, pos)) is not None:
        closers = found.get(o.group(1) if o.re.groups else "", [])
        k = bisect_left(closers, o.end(), key=lambda c: c.start())
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
    """A heredoc: ``opener``, a body, then a line holding only the opener's delimiter (``word`` is its group).

    ``full`` is the one regex of the whole heredoc and ``lines`` finds a line of one word (group 1).
    """

    full: re.Pattern[str]
    opener: re.Pattern[str]
    lines: re.Pattern[str]
    word: int

    def matches(self, text: str) -> Iterator[re.Match[str]]:
        """The matches of ``full``, but an opener whose delimiter has no line after it is skipped, not rescanned.

        A delimiter may be cut short by the regex (``<<AB`` closed by a line ``A``), so every prefix of it counts.
        """
        last: dict[str, int] = {}
        for line in self.lines.finditer(text):
            last[line.group(1)] = line.start()
        lengths = sorted({len(w) for w in last}, reverse=True)
        pos = 0
        while (o := self.opener.search(text, pos)) is not None:
            word = o.group(self.word)
            if any(last.get(word[:n], -1) >= o.end() for n in lengths if n <= len(word)):
                m = self.full.match(text, o.start())
                if m is not None:
                    yield m
                    pos = m.end()
                    continue
            pos = o.start() + 1

    def sub(self, repl: Callable[[re.Match[str]], str], text: str) -> str:
        """``full.sub(repl, text)``."""
        out, pos = [], 0
        for m in self.matches(text):
            out += [text[pos:m.start()], repl(m)]
            pos = m.end()
        return "".join(out) + text[pos:]
