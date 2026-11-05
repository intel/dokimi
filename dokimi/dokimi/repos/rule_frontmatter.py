# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The frontmatter of a ``.claude/rules`` file, read as Claude Code reads it: whether ``paths`` names a pattern."""

from __future__ import annotations

import re

import yaml

_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)^---[ \t]*(?:\r?\n|\Z)", re.S | re.M)
# The `paths:` key of frontmatter that does not parse: its line, read on its own (see _paths_block).
_PATHS_LINE = re.compile(r"^paths[ \t]*:(.*)$")
_INDENTED_KEY = re.compile(r"^[ \t]+(?:[A-Za-z0-9_][^:#]*|\"[^\"]*\"|'[^']*'):(?:[ \t]|$)")  # `  unused: ...`: a key, not a paths continuation


def _without_frontmatter(text: str) -> str:
    text = text.removeprefix("\ufeff")
    m = _FRONTMATTER.match(text)
    return text[m.end():] if m else text


def _has_paths(frontmatter: str) -> bool:
    """True if ``paths`` names at least one pattern: a string, or a list item that is a string, not blank.

    A value that is not a string (a number, a boolean, a mapping) names no pattern, and a container is never
    converted to text: a YAML alias chain expands on ``str()``. When the frontmatter does not parse, the
    ``paths`` key is read on its own (``_paths_block``), as Claude Code reads it.
    """
    try:
        meta = yaml.safe_load(frontmatter)
    except (yaml.YAMLError, RecursionError, ValueError):  # RecursionError: nesting too deep; ValueError: an integer over the digit limit
        meta = None  # Claude Code still reads a `paths:` line of YAML that does not parse
    if isinstance(meta, dict):
        return _value_names(meta.get("paths"))
    block = _paths_block(frontmatter)
    if block is None:
        return False
    try:
        own = yaml.safe_load("\n".join(block))
    except (yaml.YAMLError, RecursionError, ValueError):
        own = None
    if isinstance(own, dict):  # the key parses on its own, whatever the rest of the frontmatter does
        return _value_names(own.get("paths"))
    # the key itself does not parse (`[unclosed`): its text names a pattern unless every part is empty
    values = [_without_comment(block[0].split(":", 1)[1])]
    for line in block[1:]:
        s = line.strip()
        if s and not s.startswith("#"):
            values.append(_without_comment(s[1:] if s.startswith("-") else s))
    return any(_names_pattern(v) for v in values)


def _value_names(value: object) -> bool:
    """True if a parsed ``paths`` value names a pattern: a string (split on commas) or a list with a non-blank string."""
    items = value if isinstance(value, list) else value.split(",") if isinstance(value, str) else []
    return any(isinstance(v, str) and v.strip() for v in items)


def _names_pattern(value: str) -> bool:
    """True if one unparsed ``paths`` value (the inline value or a list item) names a pattern.

    Brackets and commas are split as YAML would; an empty item, ``null`` and ``~`` name nothing, and quotes
    around an item are not part of it (``"null"`` is the pattern ``null``).
    """
    for part in value.strip().strip("[]").split(","):
        part = part.strip()
        if part in ("", "null", "~"):
            continue
        if part.strip("\"'").strip():
            return True
    return False


def _paths_block(frontmatter: str) -> list[str] | None:
    """The lines of the ``paths`` key: its line, then the lines that continue it (indented, ``- item``, blank or comment).

    None when there is no ``paths`` key. A flow list that runs over several lines (``paths: [`` then ``"src/**"]``)
    is one block, so its continuation lines are read with the key.
    """
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        if _PATHS_LINE.match(line):
            block = [line]
            depth = _flow_depth(line)  # a flow list open on the key runs on, unindented or not
            for item in lines[i + 1:]:
                if depth == 0 and _INDENTED_KEY.match(item):  # an indented field of another key ends the block
                    break
                if depth > 0 or not item.strip() or item[:1] in (" ", "\t", "-", "#"):
                    block.append(item)
                    depth += _flow_depth(item)
                else:
                    break
            return block
    return None


def _flow_depth(line: str) -> int:
    """Open minus closed brackets on a line, a YAML comment left out (``paths: [] # [x`` is closed)."""
    text = _without_comment(line)
    return text.count("[") - text.count("]")


def _without_comment(text: str) -> str:
    """``text`` up to a YAML comment: a `#` that starts a word, outside a quoted scalar (``"src/#x"`` keeps it)."""
    quote = None
    prev = " "
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and prev in " \t[,":
            quote = ch
        elif ch == "#" and prev in " \t":
            return text[:i]
        prev = ch
    return text
