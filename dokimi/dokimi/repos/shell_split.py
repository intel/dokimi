# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A shell script split into its parts, for :func:`~dokimi.repos.cli_traces.shell_reads`."""

from __future__ import annotations

import re
from typing import NamedTuple

# ``case`` and ``esac`` as words; a ``case`` takes a word after it, so ``a|case)`` is a pattern
_CASE_WORD = re.compile(r"case(?=[ \t\n])|esac(?=[\s;&|)`]|$)")
_KEYWORD_END = re.compile(r"(?<![^\s;&|()`])(?:if|then|else|elif|do|while|until|!|\{|time)$")


class Part(NamedTuple):
    """One part of a script: its text, the operator after it, whether it is in a subshell, and its outer text."""

    text: str
    after: str  # ``&&``, ``||``, ``;``, ``&``, or ``""`` after the last part (a newline is ``;``)
    in_subshell: bool  # inside ``( ... )`` or backticks, or holding a parenthesis or a backtick
    outer: str  # the text in the script's own shell: what is inside ``( ... )`` or backticks is left out


def split_script(script: str) -> list[Part]:
    """Each part of ``script``, split at unquoted ``&&``, ``||``, ``;``, ``&`` and newlines; not inside a ``$( ... )`` or
    backtick substitution, whose separators are its own (``cd $(printf sub; true) | cat`` is one part, piped). A ``#``
    that starts a word begins a comment, which runs to the end of the line and is dropped. A part inside ``( ... )``,
    or holding a parenthesis, is in a subshell."""
    splitter = _Splitter(script)
    i = 0
    while i < len(script):
        i = splitter.step(i)
    splitter.flush("")
    return splitter.parts


def substitution_end(text: str, at: int) -> int:
    """The index past the ``)`` that closes a ``$(`` just before ``at``, as :func:`split_script` reads it: nested
    parentheses counted, a ``case`` pattern's ``)`` not (``$(case $x in a) echo;; esac)``); the end if none does."""
    splitter = _Splitter(text)
    splitter.opened.append("$(")
    while at < len(text) and splitter.opened:
        at = splitter.step(at)
    return at


class _Splitter:
    """The state of ``split_script``: the parts so far, the one being built, the open quote and parentheses."""

    def __init__(self, script: str) -> None:
        self.script = script
        self.parts: list[Part] = []
        self.cur: list[str] = []
        self.outer: list[str] = []  # the text of the part being built that is outside any parenthesis or backtick
        self.quote = ""
        self.opened: list[str] = []  # the open ``$(``, ``(`` and, inside a ``$(``, ``case``, innermost last
        self.paren = False  # the part being built holds a parenthesis
        self.tick = False  # inside a backtick substitution, a subshell as ``$( )`` is
        self.pattern_end = -1  # the index of the last ``case`` pattern's ``)``, after which a command word starts

    def add(self, text: str) -> None:
        self.cur.append(text)
        if not self.opened and not self.tick:
            self.outer.append(text)

    def flush(self, op: str) -> None:
        """End the part being built at ``op``."""
        text = "".join(self.cur).strip()
        if text:
            in_subshell = self.paren or bool(self.opened) or self.tick
            self.parts.append(Part(text, op if op != "\n" else ";", in_subshell, "".join(self.outer).strip()))
        self.cur, self.outer = [], []
        self.paren = bool(self.opened) or self.tick

    def step(self, i: int) -> int:
        """Take the text at ``i``; the index to continue at."""
        script, c = self.script, self.script[i]
        if self.quote:
            return self._quoted(i)
        if c in "'\"":
            self.quote = c
            self.add(c)
        elif c == "\\" and i + 1 < len(script):
            self.add(c + script[i + 1])
            return i + 2
        elif c == "#" and (i == 0 or script[i - 1] in " \t\n;&|()"):
            end = script.find("\n", i)  # the comment ends at the newline, which still splits
            return len(script) if end < 0 else end
        elif c == "`":
            self.paren, self.tick = True, not self.tick
            self.cur.append(c)
        elif c in "()":
            self._paren(i)
        else:
            return self._operator(i)
        return i + 1

    def _paren(self, i: int) -> None:
        """Take the parenthesis at ``i``; a ``)`` in an open ``case`` ends a pattern, not what holds the case."""
        self.paren = True
        top = self.opened[-1:]
        if self.script[i] == "(":
            self.opened.append("$(" if self.script[i - 1:i] == "$" else "(")
        elif top not in ([], ["case"]):
            self.opened.pop()
        if self.script[i] == ")" and top != ["$("] and self.opened[-1:] == ["case"]:
            self.pattern_end = i
        self.cur.append(self.script[i])

    def _case_word(self, i: int) -> str:
        """``case`` or ``esac`` at ``i`` as a command word inside a ``$(``, which opens or closes a ``case`` there;
        ``""`` for any other text."""
        m = _CASE_WORD.match(self.script, i) if "$(" in self.opened else None
        if not m or not self._command_word(i):
            return ""
        if m.group() == "case":
            self.opened.append("case")
        elif self.opened[-1] == "case":
            self.opened.pop()
        return m.group()

    def _command_word(self, i: int) -> bool:
        """Whether the word at ``i`` is a command word: first in a list (``$(case``, ``;; esac``), after a ``case``
        pattern's ``)``, or after a blank and a keyword that is one (``then case``, not ``echo then case``)."""
        while True:
            before = self.script[max(0, i - 64):i]
            rest = before.rstrip(" \t")
            if not rest or rest[-1] in ";&|(`\n":
                return True
            if rest[-1] == ")":
                return i - len(before) + len(rest) - 1 == self.pattern_end
            keyword = _KEYWORD_END.search(rest) if rest != before else None
            if keyword is None:
                return False
            i = i - len(before) + keyword.start()

    def _quoted(self, i: int) -> int:
        script, c = self.script, self.script[i]
        self.add(c)
        if c == "\\" and self.quote == '"' and i + 1 < len(script):
            i += 1
            self.add(script[i])
        elif c == self.quote:
            self.quote = ""
        return i + 1

    def _operator(self, i: int) -> int:
        script, c = self.script, self.script[i]
        two = script[i:i + 2]
        op = two if two in ("&&", "||") else c if c in ";\n" else ""
        if c == "&" and not op and script[i - 1:i] not in (">", "<") and script[i + 1:i + 2] != ">":
            op = "&"
        if not op and (word := self._case_word(i)):
            self.add(word)
            return i + len(word)
        if not op or self.tick or "$(" in self.opened:  # a substitution's own list: its text stays in the part
            self.add(c)
            return i + 1
        self.flush(op)
        return i + len(op)
