# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Where a ``catalog check`` finding sits in a ``SKILL.md``: the 1-based line the finding row carries (#942)."""

from __future__ import annotations

import ast
import re
from typing import Optional

_BLOCKQUOTE = re.compile(r"^(?: {0,3}>)+ ?")
_QUOTED_START = re.compile(r"""^('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")""")


def _lines(text: str) -> list[str]:
    """The lines of ``text``; a leading UTF-8 BOM is not part of the first (the loader accepts it).

    Only CR LF, CR and LF end a line, as in an editor: ``str.splitlines`` also splits at U+2028, a form feed
    and the other separators it knows, which would put every later finding on a wrong line."""
    return re.split(r"\r\n|\r|\n", text.removeprefix("\ufeff"))


def _frontmatter_end(lines: list[str]) -> Optional[int]:
    """The 0-based index of the line that closes the frontmatter, or None when there is none."""
    if not lines or lines[0].strip() != "---":
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return index
    return None


def key_line(text: str, key: str) -> Optional[int]:
    """The 1-based line of the top-level frontmatter ``key`` in ``text`` (written bare or quoted), or None."""
    lines = _lines(text)
    end = _frontmatter_end(lines)
    if end is None:
        return None
    first = next((l for l in lines[1:end] if l.strip() and not l.lstrip().startswith("#")), "")
    indent = re.escape(first[: len(first) - len(first.lstrip())])  # a block indented as a whole is still top level
    written = re.compile(rf"""^{indent}(?:{re.escape(key)}|"{re.escape(key)}"|'{re.escape(key)}')\s*:""")
    for number in range(2, end + 1):
        if written.match(lines[number - 1]):
            return number
    return None


def text_line(text: str, *needles: "str | tuple[str, ...]") -> Optional[int]:
    """The 1-based line after the frontmatter of the first match, or None.

    The arguments are tried in order and the first that is found wins; an argument that is a tuple is one
    group, whose earliest line counts (so ``]: x`` above ``(x)`` is the first occurrence of the same target).
    """
    lines = _lines(text)
    end = _frontmatter_end(lines)
    first = 0 if end is None else end + 1
    for group in needles:
        group = (group,) if isinstance(group, str) else group
        for index in range(first, len(lines)):
            if any(needle in lines[index] for needle in group):
                return index + 1
    return None


def _unquoted(lines: list[str]) -> str:
    """``lines`` joined with their blockquote markers dropped (the line count is kept). A line break between lines of
    a different quote depth is marked with a NUL, since a link cannot continue from a quote into a paragraph."""
    out: list[str] = []
    previous = 0
    for index, line in enumerate(lines):
        marker = _BLOCKQUOTE.match(line)
        depth = marker.group(0).count(">") if marker else 0
        if index and depth != previous:
            out[-1] += "\x00"
        previous = depth
        out.append(line[marker.end():] if marker else line)
    return "\n".join(out)


def link_line(text: str, raw: str) -> Optional[int]:
    """The 1-based line after the frontmatter of the first link whose destination is exactly ``raw``, or None.

    An inline ``(raw)``, ``( raw )``, ``(raw "title")`` or ``(<raw>)`` and a reference ``]: raw`` all count, but
    a destination that only starts with ``raw`` (``]: raw.bak``) does not. A blockquote marker is ignored (but a link does not continue into or out of a quote) and the
    destination may be on the line after its opener, never past a blank line (the destination's line is returned);
    a line that merely contains ``raw`` is the fallback when no link form matches.
    """
    lines = _lines(text)
    end = _frontmatter_end(lines)
    first = 0 if end is None else end + 1
    body = _unquoted(lines[first:])
    word = re.escape(raw)
    form = re.compile(
        rf"(?:\]\(|^ {{0,3}}\[[^\]]*\]:)[ \t]*(?:\n[ \t]*)?(?:<(?P<a>{word})>|(?P<b>{word})(?=[\s)\x00]|$))",
        re.MULTILINE,
    )  # inside <...> only ">" ends it; the destination may sit on a line after its opener
    found = form.search(body)
    if found:
        start = found.start("a") if found.group("a") is not None else found.start("b")
        return first + body.count("\n", 0, start) + 1
    return text_line(text, raw)


def written_path(message: str) -> str:
    """The path a ``path-missing`` message names: the Python string literal its message opens with."""
    match = _QUOTED_START.match(message)
    return str(ast.literal_eval(match.group(1))) if match else message


def path_needles(message: str) -> tuple[str, ...]:
    """What to look for in ``SKILL.md`` for a ``path-missing`` message, best first: the code span the path was in
    (``(in '...')``, as written, so ``my\\ tool.py`` is found), then the path alone."""
    path = written_path(message)
    rest = message[len(repr(path)):] if message.startswith(repr(path)) else ""
    span = _QUOTED_START.match(rest.removeprefix(" (in ")) if rest.startswith(" (in ") else None
    written = str(ast.literal_eval(span.group(1))) if span else path
    return (f"`{written}`", written, path.replace(" ", "\\ "), path)
