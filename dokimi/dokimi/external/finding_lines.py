# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Where a ``catalog check`` finding sits in a ``SKILL.md``: the 1-based line the finding row carries (#942)."""

from __future__ import annotations

import ast
import re
from typing import Optional

_QUOTED_START = re.compile(r"""^('(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")""")


def _lines(text: str) -> list[str]:
    """The lines of ``text``; a leading UTF-8 BOM is not part of the first (the loader accepts it)."""
    return text.removeprefix("\ufeff").splitlines()


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
    written = re.compile(rf"""^(?:{re.escape(key)}|"{re.escape(key)}"|'{re.escape(key)}')\s*:""")
    for number in range(2, end + 1):
        if written.match(lines[number - 1]):
            return number
    return None


def text_line(text: str, *needles: str) -> Optional[int]:
    """The 1-based line of the first line after the frontmatter that holds the first of ``needles`` found, or None."""
    lines = _lines(text)
    end = _frontmatter_end(lines)
    first = 0 if end is None else end + 1
    for needle in needles:
        for index in range(first, len(lines)):
            if needle in lines[index]:
                return index + 1
    return None


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
