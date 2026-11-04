# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A shell script split into its parts, for :func:`~dokimi.repos.cli_traces.shell_reads`."""

from __future__ import annotations

from typing import NamedTuple


class Part(NamedTuple):
    """One part of a script: its text, the operator after it, whether it is in a subshell, and its outer text."""

    text: str
    after: str  # ``&&``, ``||``, ``;``, ``&``, or ``""`` after the last part (a newline is ``;``)
    in_subshell: bool  # inside ``( ... )`` or backticks, or holding a parenthesis or a backtick
    outer: str  # the text in the script's own shell: what is inside ``( ... )`` or backticks is left out


def split_script(script: str) -> list[Part]:
    """Each part of ``script``, split at unquoted ``&&``, ``||``, ``;``, ``&`` and newlines. A ``#`` that
    starts a word begins a comment, which runs to the end of the line and is dropped. A part inside
    ``( ... )``, or holding a parenthesis, is in a subshell."""
    splitter = _Splitter(script)
    i = 0
    while i < len(script):
        i = splitter.step(i)
    splitter.flush("")
    return splitter.parts


class _Splitter:
    """The state of ``split_script``: the parts so far, the one being built, the open quote and parentheses."""

    def __init__(self, script: str) -> None:
        self.script = script
        self.parts: list[Part] = []
        self.cur: list[str] = []
        self.outer: list[str] = []  # the text of the part being built that is outside any parenthesis or backtick
        self.quote = ""
        self.depth = 0  # open parentheses
        self.paren = False  # the part being built holds a parenthesis
        self.tick = False  # inside a backtick substitution, a subshell as ``$( )`` is

    def add(self, text: str) -> None:
        self.cur.append(text)
        if not self.depth and not self.tick:
            self.outer.append(text)

    def flush(self, op: str) -> None:
        """End the part being built at ``op``."""
        text = "".join(self.cur).strip()
        if text:
            in_subshell = self.paren or self.depth > 0 or self.tick
            self.parts.append(Part(text, op if op != "\n" else ";", in_subshell, "".join(self.outer).strip()))
        self.cur, self.outer = [], []
        self.paren = self.depth > 0 or self.tick

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
            self.paren = True
            self.depth = max(0, self.depth + (1 if c == "(" else -1))
            self.cur.append(c)
        else:
            return self._operator(i)
        return i + 1

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
        if not op:
            self.add(c)
            return i + 1
        self.flush(op)
        return i + len(op)
