# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Guidance rendered in another format: the ``guidance:format=<fmt>`` arm (contract §3, step 5).

``xml`` wraps a guidance file in a ``<guidance>`` element and turns each ATX heading into a
``<section level=".." title="..">`` element, closed before the next heading of its level or
above. No other line changes, and :func:`restore` undoes the wrapping exactly. :func:`to_format`
refuses a file whose rendering does not restore to it (a body line that looks like one of the
tags, a heading the restore could not rebuild), so the arm never measures altered content.
Headings inside code fences are body text.
"""

from __future__ import annotations

import re

#: The formats of ``guidance:format=<fmt>``. Markdown is ``guidance:raw``, not a format of its own.
FORMATS = ("xml",)

_ROOT, _ROOT_CLOSE, _CLOSE = "<guidance>", "</guidance>", "</section>"
_HEADING = re.compile(r"^(#{1,6}) (\S[^\r]*)$")  # canonical only: one space, no CR; anything else is body text
_FENCE = re.compile(r"^( *)(`{3,}|~{3,})(.*)$")  # the indentation, a run of ``` or ~~~, and what follows it
_ITEM = re.compile(r" *(?:[-*+]|(\d{1,9})[.)])(?: {1,4}(?=\S)|(?=\s*$))")  # a list marker (its number if ordered), then 1-4 spaces and content, or nothing (an empty item)
_RULE = re.compile(r" *([-*_])(?: *\1){2,} *$")  # a thematic break: no list item, though ``- - -`` looks like two
_OPENER = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")  # up to three spaces, then a run that opens a fence
_BLOCK = re.compile(r" *(?:#{1,6}(?: |$)|>|([-*_])( *\1){2,} *$)")  # a heading, quote or rule: a line that starts a block
_OPEN = re.compile(r'^<section level="([1-6])" title="([^"]*)">$')
_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}
_UNESCAPES = {v: k for k, v in _ESCAPES.items()}


class FormatError(ValueError):
    """A guidance file that cannot be rendered in the format, or a rendering that would not restore."""


def to_format(text: str, fmt: str) -> str:
    """``text`` (Markdown) rendered as ``fmt``; raises :class:`FormatError` unless :func:`restore` gives ``text``."""
    if fmt not in FORMATS:
        raise FormatError(f"unknown guidance format {fmt!r} (known: {', '.join(FORMATS)})")
    rendered = _render_xml(text)
    if restore(rendered) != text:
        raise FormatError(f"the {fmt} rendering would not restore to the file (a line of it looks like a tag)")
    return rendered


def restore(rendered: str) -> str:
    """The Markdown a rendering came from: the wrapper lines removed, each section tag back to its heading."""
    lines = rendered.split("\n")
    if len(lines) < 2 or lines[0] != _ROOT or lines[-1] != _ROOT_CLOSE:
        raise FormatError("not a guidance rendering: the root element is missing")
    out = []
    for line in lines[1:-1]:
        if line == _CLOSE:
            continue
        match = _OPEN.match(line)
        out.append(f"{'#' * int(match.group(1))} {_unescape(match.group(2))}" if match else line)
    return "\n".join(out)


def _marker_columns(line: str, para: bool = False) -> tuple[int, ...]:
    """The content columns of the list markers that start a line, outermost first (``- - x`` gives two). A thematic
    break starts none. After paragraph text (``para``) the outermost marker starts an item only if CommonMark lets it
    interrupt a paragraph: a bullet with content, or an ordered marker numbered 1; otherwise the line is paragraph
    text and no marker starts."""
    expanded = line.expandtabs(4)  # a tab after a marker is spaces up to the next column, as in _opened
    if _RULE.match(expanded):
        return ()
    columns: list[int] = []
    while (item := _ITEM.match(expanded, columns[-1] if columns else 0)) is not None:
        empty = not expanded[item.end():].strip()  # an empty item's content starts one column past its marker
        if not columns and para and (empty or (item.group(1) is not None and int(item.group(1)) != 1)):
            return ()
        columns.append(item.end() + (1 if empty else 0))
    return tuple(columns)


def _markers(items: tuple[int, ...], line: str, para: bool) -> tuple[int, ...]:
    """The list markers a line starts, given the items open before it. The interrupt rule of `_marker_columns` applies
    only when the paragraph is inside the containers the line continues: an item the line is indented less than is
    not continued, and a marker that follows its paragraph there starts a list."""
    expanded = line.expandtabs(4)
    indent = len(expanded) - len(expanded.lstrip(" "))
    return _marker_columns(line, para and (not items or indent >= items[-1]))


def _items_after(items: tuple[int, ...], line: str, para: bool = False) -> tuple[int, ...]:
    """The list items open after a line read outside a fence, outermost first. A blank line keeps them. A non-blank line
    ends the items whose content column it is indented less than, and a list marker it starts opens one item per
    marker at its content column. A line that continues paragraph text (``para``) ends nothing, unless it starts a
    block (a heading, quote or rule, or a fence) or a list item."""
    if not line.strip():
        return items
    expanded = line.expandtabs(4)
    indent = len(expanded) - len(expanded.lstrip(" "))
    markers = _markers(items, line, para)
    if not markers and para and not _BLOCK.match(expanded) and not _OPENER.match(expanded):
        return items
    return tuple(column for column in items if column <= indent) + markers


def _opened(line: str, items: tuple[int, ...]) -> tuple[str, int] | None:
    """The fence a line opens, as (its run, the column its content starts at), or None. The fence takes the content
    column of the innermost item open at the line (``items``, from `_items_after`), so a fence on a list marker line
    (``- ```, ``- - ```) and one on a continuation line both sit inside their item. The line is read expanded, a tab
    counting to four columns. As in CommonMark, a backtick fence's info string has no backtick."""
    column = items[-1] if items else 0
    match = _OPENER.match(line.expandtabs(4), column)
    if match is None or (match.group(1)[0] == "`" and "`" in match.group(2)):
        return None
    return match.group(1), column


def _closes(fence: tuple[str, int], line: str) -> bool:
    """Whether a line closes the fence: a run of its character, at least as long as the opening one, followed by
    nothing but whitespace, indented at most three columns past the item's content column."""
    run, column = fence
    match = _FENCE.match(line.expandtabs(4))
    return bool(match and match.group(2)[0] == run[0] and len(match.group(2)) >= len(run)
                and not match.group(3).strip() and len(match.group(1)) <= column + 3)


def _advance(fence: tuple[str, int] | None, items: tuple[int, ...], line: str, para: bool = False,
             empty: bool = False) -> tuple[tuple[str, int] | None, tuple[int, ...], bool, bool]:
    """The fence open after a line, the list items open after it, whether the line is paragraph text (which a
    following less indented line may continue lazily), and whether it is an empty list item. A line indented less than
    the column of the item that opened the fence ends the item, and the fence with it; the line is then read as any
    other line. An indented code block is not paragraph text. A blank line ends an empty item (CommonMark: an item
    begins with at most one blank line)."""
    if fence is not None:
        expanded = line.expandtabs(4)  # a tab is indentation up to the next multiple of four
        if not (line.strip() and len(expanded) - len(expanded.lstrip(" ")) < fence[1]):
            return (None if _closes(fence, line) else fence), items, False, False
    if not line.strip():
        return None, (items[:-1] if empty else items), False, False
    markers = _markers(items, line, para)
    items = _items_after(items, line, para)
    opened = _opened(line, items)
    expanded = line.expandtabs(4)
    text = expanded[markers[-1]:].strip() if markers else expanded.strip()
    indent = len(expanded) - len(expanded.lstrip(" "))
    code = not para and indent - (items[-1] if items else 0) >= 4  # four columns past the content column: code
    return (opened, items, opened is None and bool(text) and not _BLOCK.match(text) and not code,
            bool(markers) and not text)


def _render_xml(text: str) -> str:
    out = [_ROOT]
    fence: tuple[str, int] | None = None
    items: tuple[int, ...] = ()
    para = empty = False
    open_levels: list[int] = []
    for line in text.split("\n"):
        fence, items, para, empty = _advance(fence, items, line, para, empty)
        heading = _HEADING.match(line) if fence is None else None  # a line of a fence is body text
        if heading is None:
            out.append(line)
            continue
        level = len(heading.group(1))
        while open_levels and open_levels[-1] >= level:
            out.append(_CLOSE)
            open_levels.pop()
        out.append(f'<section level="{level}" title="{_escape(heading.group(2))}">')
        open_levels.append(level)
    out.extend([_CLOSE] * len(open_levels))
    out.append(_ROOT_CLOSE)
    return "\n".join(out)


def _escape(title: str) -> str:
    return re.sub(r'[&<>"]', lambda m: _ESCAPES[m.group()], title)


def _unescape(title: str) -> str:
    return re.sub(r"&(?:amp|lt|gt|quot);", lambda m: _UNESCAPES[m.group()], title)
