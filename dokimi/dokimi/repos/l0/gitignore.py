# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which paths a snapshot's ``.gitignore`` files ignore, read from the tree like every L0 fact.

A missing path the repository ignores is a build output the guidance warns about,
not a rename: L0 reports it ``unresolved``. Only tracked ``.gitignore`` files count;
``.git/info/exclude`` and ``core.excludesFile`` are per clone and not part of the snapshot.

The rules follow gitignore(5): ``#`` comments and blank lines skipped, ``!``
re-includes, a trailing ``/`` matches directories only, a pattern with a ``/``
before its end is anchored to its file's directory and one without matches a
name at any depth, ``*`` and ``?`` stay within one segment, ``**`` spans
segments, the last matching line wins with deeper files read after shallower
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

    def matches(self, path: str, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        if self.base:
            if not path.startswith(self.base + "/"):
                return False
            path = path[len(self.base) + 1:]
        return bool(self.regex.match(path if self.anchored else posixpath.basename(path)))


def _segment(pat: str) -> str:
    """Regex for a glob within path segments: ``*`` and ``?`` never cross ``/``."""
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "\\" and i + 1 < len(pat):
            out.append(re.escape(pat[i + 1]))
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[" and (end := pat.find("]", i + 2)) != -1:
            body = pat[i + 1:end]
            neg = body[:1] in ("!", "^")
            out.append("[" + ("^" if neg else "") + body[neg:].replace("\\", "\\\\") + "]")
            i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _regex(pat: str) -> str:
    """``**/`` leading, ``/**/`` inside and ``/**`` trailing span segments; any other ``**`` is ``*``."""
    parts = pat.split("/")
    out: list[str] = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
            continue
        out.append(_segment(part) + ("" if last else "/"))
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
        rules.append(Rule(source, n, raw.strip(), base, re.compile(_regex(pat.lstrip("/")) + r"\Z"),
                          anchored, dir_only, negated))
    return rules


class GitIgnore:
    """The ignore rules of every ``.gitignore`` in a tree."""

    def __init__(self, files: Iterable[str], read: Callable[[str], str]) -> None:
        sources = sorted((f for f in files if posixpath.basename(f) == ".gitignore"),
                         key=lambda f: (f.count("/"), f))
        self.rules = [r for f in sources for r in parse(f, read(f))]

    def _last(self, path: str, is_dir: bool) -> Rule | None:
        return next((r for r in reversed(self.rules) if r.matches(path, is_dir)), None)

    def ignored_by(self, path: str, is_dir: bool = False) -> Rule | None:
        """The rule that ignores ``path`` or one of its directories, or ``None`` when git would not."""
        parts = posixpath.normpath(path).strip("/").split("/")
        for i in range(1, len(parts) + 1):
            rule = self._last("/".join(parts[:i]), is_dir or i < len(parts))
            if rule is not None and not rule.negated:
                return rule
        return None
