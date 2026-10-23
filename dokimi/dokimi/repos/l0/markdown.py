# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Split a guidance file into the pieces L0 checks: links, inline code spans, fenced code.

Only structure is recognised here; what a piece claims is decided by the
checks. Line numbers are 1-based, as in an editor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SHELL_LANGS = frozenset({"", "bash", "sh", "shell", "console", "zsh"})  # not `text`: that is output
STARLARK_LANGS = frozenset({"starlark", "bzl", "bazel", "build"})
# Starlark is a Python dialect, so a BUILD snippet is often fenced as Python; such a fence is checked when it reads
# like BUILD code (see ``adapters.reads_as_build``).
PYTHON_LANGS = frozenset({"python", "py", "python3"})

# A target may hold balanced parentheses, as in `a_(b).md`, or be `<in angle brackets>` with spaces;
# a title is "double", 'single' or (parenthesised).
_TITLE = r"""(?:\s+(?:"[^"]*"|'[^']*'|\([^()]*\)))?"""
_LINK_RE = re.compile(rf"\]\(\s*(<[^<>\n]*>|(?:[^()\s]|\([^()\s]*\))+){_TITLE}\s*\)")
# A reference definition, `[label]: target "title"`, on a line of its own. Not a footnote (`[^1]: text`).
_REFDEF_RE = re.compile(rf"^ {{0,3}}\[(?!\^)[^\]]+\]:\s*(<[^<>\n]*>|\S+){_TITLE}\s*$")
_SPAN_RE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)\s*([\w+-]*)")


@dataclass
class Fence:
    line: int  # line of the opening fence
    lang: str
    lines: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Pieces:
    links: list[tuple[int, str, str]] = field(default_factory=list)  # (line, target, line text)
    spans: list[tuple[int, str, str]] = field(default_factory=list)  # (line, span, line text)
    fences: list[Fence] = field(default_factory=list)


def split(text: str) -> Pieces:
    """Links and spans outside fences, and every fenced block with its language."""
    out = Pieces()
    fence: Fence | None = None
    marker = ""
    for n, line in enumerate(text.splitlines(), 1):
        m = _FENCE_RE.match(line)
        if m and m.group(1)[0] == "`" and "`" in line[m.end(1):]:
            m = None  # CommonMark: a backtick fence's info string has no backticks, so this is inline code
        if fence is None and m:
            marker = m.group(1)
            fence = Fence(n, m.group(2).lower())
            continue
        if fence is not None:
            if line.strip().startswith(marker[0] * len(marker)) and not line.strip().strip(marker[0]):
                out.fences.append(fence)
                fence = None
            else:
                fence.lines.append((n, line))
            continue
        prose = _SPAN_RE.sub("", line)  # not in code
        ref = _REFDEF_RE.match(prose)
        out.links.extend((n, t, line) for t in ([ref.group(1)] if ref else _LINK_RE.findall(prose)))
        out.spans.extend((n, s.strip(), line) for s in _SPAN_RE.findall(line) if s.strip())
    if fence is not None:  # unterminated: still checked
        out.fences.append(fence)
    return out
