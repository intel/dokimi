# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which paths a snapshot's ``.gitignore`` files ignore, read from the tree like every L0 fact.

A missing path the repository ignores is a build output the guidance warns about,
not a rename: L0 reports it ``unresolved``. Only tracked ``.gitignore`` files count;
``.git/info/exclude`` and ``core.excludesFile`` are per clone and not part of the snapshot.

The rules follow gitignore(5): ``#`` comments and blank lines skipped, ``!``
re-includes, a trailing ``/`` matches directories only, a pattern with a ``/``
before its end is anchored to its file's directory and one without matches a
name at any depth, ``*``, ``?`` and ``[...]`` stay within one segment, ``**`` spans
segments, a line git's wildmatch aborts on matches nothing, the last matching line wins with deeper files read after shallower
ones, and nothing under an ignored directory can be re-included.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class Rule:
    source: str  # the .gitignore path
    line: int
    pattern: str  # the line as written
    base: str  # the directory the file is in, "" at the root
    regex: re.Pattern[str]
    anchored: bool
    dir_only: bool
    negated: bool
    name: str = ""  # the last segment of the pattern, wildcards kept; "" for a line git cannot read

    def matches(self, path: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if self.base:
            if not path.startswith(self.base + "/"):
                return False
            path = path[len(self.base) + 1:]
        return bool(self.regex.match(path if self.anchored else posixpath.basename(path)))


def _segment(pat: str) -> str:
    """Regex for a glob within path segments: ``*`` and ``?`` never cross ``/``.

    As in CPython's fnmatch, every ``*`` but the last takes the fixed-width run after it
    in an atomic group, so a line with many stars cannot backtrack exponentially.
    """
    runs: list[list[str]] = [[]]  # the fixed-width atoms before, between and after the stars
    i = 0
    while i < len(pat):
        c = pat[i]
        if c == "[":
            atom, i = _bracket(pat, i)
            runs[-1].append(atom)
            continue
        if c == "\\":
            i += 1
            if i == len(pat):
                raise _Invalid
            runs[-1].append(re.escape(pat[i]))
        elif c == "*":
            if len(runs) == 1 or runs[-1]:
                runs.append([])
        elif c == "?":
            runs[-1].append("[^/]")
        else:
            runs[-1].append(re.escape(c))
        i += 1
    head, *rest = ("".join(r) for r in runs)
    if not rest:
        return head
    *middle, tail = rest
    return head + "".join(f"(?>[^/]*?{m})" for m in middle) + "[^/]*" + tail


class _Invalid(Exception):
    """A line git's wildmatch aborts on (an unclosed ``[``, a trailing ``\\``): it matches nothing."""


_CLASSES = {
    "alnum": "a-zA-Z0-9", "alpha": "a-zA-Z", "blank": r" \t", "cntrl": r"\x00-\x1f\x7f",
    "digit": "0-9", "graph": "!-~", "lower": "a-z", "print": " -~", "punct": r"!-/:-@\[-`{-~",
    "space": r" \t\n\r", "upper": "A-Z", "xdigit": "0-9A-Fa-f",
}


def _bracket(pat: str, i: int) -> tuple[str, int]:
    """The regex for the ``[...]`` at ``pat[i]`` and the index after it, read as git's wildmatch reads it.

    A ``]`` first is a member, ``\\`` escapes, ``[:name:]`` is an ASCII class, a reversed
    range keeps only its start, and the set never matches ``/``.
    """
    i += 1
    neg = pat[i:i + 1] in ("!", "^")
    i += neg
    items: list[str] = []
    prev = ""
    while i < len(pat) and (pat[i] != "]" or not items):
        c = pat[i]
        if c == "\\" or (c == "-" and prev and pat[i + 1:i + 2] not in ("", "]")):
            i += 1 + (c == "-" and pat[i + 1] == "\\")
            if i == len(pat):
                raise _Invalid
            if c == "-":
                items.append(f"{re.escape(prev)}-{re.escape(pat[i])}" if prev <= pat[i] else "")
                prev = ""
            else:
                prev = pat[i]
                items.append(re.escape(prev))
        elif c == "[" and pat[i + 1:i + 2] == ":" and (end := pat.find("]", i + 2)) > i + 2 and pat[end - 1] == ":":
            if pat[i + 2:end - 1] not in _CLASSES:
                raise _Invalid
            items.append(_CLASSES[pat[i + 2:end - 1]])
            prev, i = "", end
        else:
            prev = c
            items.append(re.escape(c))
        i += 1
    if i == len(pat):
        raise _Invalid
    return "(?!/)[" + ("^" if neg else "") + "".join(items) + "]", i + 1


def _split(pat: str) -> list[str]:
    """``pat`` cut at each ``/`` outside a bracket expression."""
    parts, start, i = [], 0, 0
    while i < len(pat):
        if pat[i] == "[":
            i = _bracket(pat, i)[1]
            continue
        if pat[i] == "/":
            parts.append(pat[start:i])
            start = i + 1
        i += 1 + (pat[i] == "\\")
    return parts + [pat[start:]]


def _regex(pat: str) -> str:
    """``**/`` leading, ``/**/`` inside and ``/**`` trailing span segments; any other ``**`` is ``*``."""
    parts = _split(pat)
    out: list[str] = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
            continue
        out.append(_segment(part) + ("" if last else "/"))
    return "".join(out)


_NEVER = re.compile(r"(?!)")
_MEMBERS = "xad0_-.AZ9"  # tried in turn for a ``[...]``: the first one it matches stands for it


def example(pat: str, fill: str) -> str | None:
    """A name the one-segment ``pat`` matches: each ``*`` as ``fill``, each ``?`` as its first letter,
    each ``[...]`` as a member; ``None`` when no candidate member fits a class."""
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "[":
            atom, i = _bracket(pat, i)
            out.append(next((m for m in fill[0] + _MEMBERS if re.fullmatch(atom, m)), ""))
            if not out[-1]:
                return None
            continue
        if c == "\\":
            i += 1
            out.append(pat[i])
        else:
            out.append({"*": fill, "?": fill[0]}.get(c, c))
        i += 1
    return "".join(out)


def parse(source: str, text: str) -> list[Rule]:
    base = posixpath.dirname(source)
    rules = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = re.sub(r"(?<!\\)\s+$", "", raw)  # trailing spaces go unless escaped
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pat = line[1:] if negated else line
        if pat.startswith(("\\#", "\\!")):
            pat = pat[1:]
        dir_only = pat.endswith("/")
        pat = pat.rstrip("/")
        if not pat:
            continue
        anchored = "/" in pat
        try:
            regex, name = re.compile(_regex(pat.lstrip("/")) + r"\Z"), _split(pat.lstrip("/"))[-1]
        except _Invalid:
            regex, name = _NEVER, ""
        rules.append(Rule(source, n, line, base, regex, anchored, dir_only, negated, name))
    return rules


class GitIgnore:
    """The ignore rules of every ``.gitignore`` in a tree."""

    def __init__(self, files: Iterable[str], read: Callable[[str], str]) -> None:
        sources = sorted((f for f in files if posixpath.basename(f) == ".gitignore"),
                         key=lambda f: (f.count("/"), f))
        self.rules = [r for f in sources for r in parse(f, read(f))]

    def _last(self, path: str, is_dir: bool) -> Rule | None:
        return next((r for r in reversed(self.rules) if r.matches(path, is_dir)), None)

    def names(self, fill: str, negated: bool = False) -> list[str]:
        """A name the last segment of each ignoring rule matches (``gen_a.h`` for ``gen_[a-c].h``),
        or with ``negated`` of each ``!`` rule."""
        found = (example(r.name, fill) for r in self.rules if r.name and r.negated == negated)
        return list(dict.fromkeys(n for n in found if n))

    def ignored_by(self, path: str, is_dir: bool = False) -> Rule | None:
        """The rule that ignores ``path`` or one of its directories, or ``None`` when git would not."""
        parts = posixpath.normpath(path).strip("/").split("/")
        for i in range(1, len(parts) + 1):
            rule = self._last("/".join(parts[:i]), is_dir or i < len(parts))
            if rule is not None and not rule.negated:
                return rule
        return None
