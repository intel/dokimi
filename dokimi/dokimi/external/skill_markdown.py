# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Markdown as CommonMark reads it, for the skill checks of :mod:`dokimi.external.validate`.

:func:`outside_fences` gives the lines of a body that are not code, and :func:`link_pieces`
the link destinations and code-span texts of those lines. Both are read here, with the
block rules (fences, blockquotes, list items, paragraphs) and the link-destination rules
(parentheses up to :data:`LINK_PAREN_DEPTH_MAX` deep, escapes, reference definitions).
"""

from __future__ import annotations

import re
from typing import Optional

from dokimi.repos.l0.markdown import split as split_markdown

# A code span of any run length, across lines of a paragraph: its text is literal (a backslash escapes
# nothing in it), so no link is read inside one. Group 2 is the text.
CODE_SPAN_RE = re.compile(r"(?<![\\`])(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.DOTALL)
# Brackets and the backslashes that may escape them, in order (_unlinked_brackets).
BRACKET_RE = re.compile(r"\\\\|\\[\[\]]|[\[\]]")

# An inline link target balanced in parentheses up to 32 deep (``a(b(c)).md``), in angle brackets (no line
# break inside), with an optional title; split() reads one level of parentheses only, so these are read here.
_TARGET_TITLE = r"""(?:\s+(?:"[^"]*"|'[^']*'|\([^()]*\)))?"""
_LINK_OPEN_RE = re.compile(r"\]\(\s*")
_LINK_END_RE = re.compile(rf"{_TARGET_TITLE}\s*\)")
LINK_PAREN_DEPTH_MAX = 32
# A reference label alone on its line: its destination may be on the next line.
_LABEL_ONLY_RE = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:[ \t]*$")
# A line that starts a reference definition (its target is read by the splitter, not _inline_destinations).
_DEFINITION_START_RE = re.compile(r"^ {0,3}\[(?!\^)[^\]]+\]:")
# Blocks that end a paragraph: a list item and an ATX heading (see _paragraphs).
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")
FENCE_OPEN_RE = re.compile(r"\ {0,3}(`{3,}|~{3,})(.*)$")
FENCE_CLOSE_RE = re.compile(r"\ {0,3}(`{3,}|~{3,})[ \t]*$")
# A fence under a list item may sit deeper than three spaces (the item's indentation); its opener is
# read after the bullet, its other lines relative to the item's content column (_dedent).
DEEP_FENCE_OPEN_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)$")
QUOTE_MARK_RE = re.compile(r"[ \t]{0,3}>[ \t]?")
LIST_ITEM_RE = re.compile(r"[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]")


def outside_fences(body: str) -> list[str]:
    """The lines of ``body`` that are not inside a fenced code block, without their blockquote markers.

    A block closes only at a line of the same marker (backticks or tildes) that is at least as long
    as the opening run, as in CommonMark. A fence in a blockquote keeps only the opener's ``>``
    markers (a deeper ``>`` is code), and it ends with its quote. A fence under a list item may be
    indented past three spaces, and it ends with its innermost item: at the first non-blank line
    indented less than that item's content column. A list item's own line may open a fence (``- ```).
    Any other fence runs to its closing marker or to the end. Inside an item, a fence's marker may
    sit at most three columns past the item's content column, as outside one. A change of quote
    depth is a blank line.
    """
    lines: list[str] = []
    fence = ""  # the run that opened the current block; "" outside one
    depth = 0  # the blockquote depth of the opener
    col = 0  # the content column of the list item the opener sits in; 0 for a fence outside any item
    items: list[tuple[int, int]] = []  # (content column, blockquote depth) of the open items, outermost first
    last_depth = 0
    for line in body.splitlines():
        if fence:
            rest, count = _unquote(line, depth)
            ended = count < depth or bool(col and rest.strip() and _indent(rest) < col)
            if not ended:
                close = FENCE_CLOSE_RE.match(_dedent(rest, col))
                if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= len(fence):
                    fence = ""
                    lines.append("")  # a blank line keeps a definition above the block from reaching below it
                continue
            fence = ""  # the container ended before a closing marker: this line is outside the block
        rest, count = _unquote(line)
        # a line closes the items whose quote it has left, and the items it is not indented into
        while items and (count < items[-1][1] or (rest.strip() and _indent(rest) < items[-1][0])):
            items.pop()
        bullet = LIST_ITEM_RE.match(rest)
        if bullet:
            items.append((_width(rest[:bullet.end()]), count))
            opener = rest[bullet.end():]
        else:
            opener = rest
        if bullet:
            opened = DEEP_FENCE_OPEN_RE.match(opener)
        else:
            opened = FENCE_OPEN_RE.match(_dedent(opener, items[-1][0] if items else 0))
        # a backtick info string may not hold a backtick: "```x```" is an inline code span, not a fence
        if opened and not (opened.group(1)[0] == "`" and "`" in opened.group(2)):
            fence, depth, col = opened.group(1), count, (items[-1][0] if items else 0)
            lines.append("")
            continue
        if count != last_depth:
            lines.append("")
        last_depth = count
        lines.append(rest)
    return lines


def _unquote(line: str, depth: int | None = None) -> tuple[str, int]:
    """``line`` without its first ``depth`` blockquote markers (all of them when None), and how many it lost."""
    count = 0
    while depth is None or count < depth:
        mark = QUOTE_MARK_RE.match(line)
        if not mark:
            break
        line = line[mark.end():]
        count += 1
    return line, count


def _dedent(line: str, col: int) -> str:
    """``line`` with its leading whitespace cut back to the column ``col`` (a list item's content column)."""
    cut = 0
    while cut < len(line) and line[cut] in " \t" and _width(line[: cut + 1]) <= col:
        cut += 1
    return line[cut:]


def _width(text: str) -> int:
    """The column just past ``text``, where a tab advances to the next multiple of four, as in CommonMark."""
    col = 0
    for ch in text:
        col = (col // 4 + 1) * 4 if ch == "\t" else col + 1
    return col


def _indent(line: str) -> int:
    """The column where the text of ``line`` starts, after its leading spaces and tabs."""
    return _width(line[:len(line) - len(line.lstrip(" \t"))])


def _paragraphs(lines: list[str]) -> list[str]:
    """The paragraphs of ``lines`` (blank lines end one), each joined by newlines.

    A list item and an ATX heading are blocks of their own: a code span cannot run across them (CommonMark).
    """
    out: list[str] = []
    block: list[str] = []
    for line in lines + [""]:
        if not line.strip() or _LIST_ITEM_RE.match(line) or _HEADING_RE.match(line):
            if block:
                out.append("\n".join(block))
                block = []
        if line.strip():
            block.append(line)
            if _HEADING_RE.match(line):
                out.append("\n".join(block))
                block = []
    return out


def _unlinked_brackets(text: str) -> str:
    """``text`` with the brackets that open no link blanked: an escaped one (``\\[``), and a ``]`` with no
    unescaped ``[`` still open before it, so ``\\[guide](x.md)`` and ``note](x.md)`` read no link while
    ``[![CI](badge.svg)](x.md)`` and ``[see [docs]](x.md)`` keep both. An escaped backslash is kept, so a
    bracket after it still counts."""
    out: list[str] = []
    open_count, pos = 0, 0
    for m in BRACKET_RE.finditer(text):
        token = m.group()
        out.append(text[pos : m.start()])
        pos = m.end()
        if token == "\\\\":
            out.append(token)
        elif token[0] == "\\":
            out.append("  ")
        elif token == "[":
            open_count += 1
            out.append(token)
        elif open_count:
            open_count -= 1
            out.append(token)
        else:
            out.append(" ")
    out.append(text[pos:])
    return "".join(out)


def _destination_stop(line: str, start: int) -> Optional[int]:
    """Where the link destination that begins at ``start`` ends, or None if no destination is there."""
    if start < len(line) and line[start] == "<":
        end = line.find(">", start + 1)
        return None if end == -1 or any(c in line[start + 1 : end] for c in "<\n") else end + 1
    depth, stop = 0, start
    while stop < len(line):
        char = line[stop]
        if char == "\\":
            stop += 2
            continue
        if char.isspace() or (char == ")" and depth == 0):
            break
        if char == "(":
            depth += 1
            if depth > LINK_PAREN_DEPTH_MAX:
                return None  # cmark caps it too; unbounded, each ``](`` of a hostile body scans to its end
        elif char == ")":
            depth -= 1
        stop += 1
    return None if depth or stop == start else stop


def _inline_destinations(text: str) -> list[str]:
    """The destinations of the inline links in ``text``, as written (``<...>`` kept, a title left out).

    A ``](`` opens a destination that runs to its matching parenthesis (up to 32 deep), or to the ``>``
    of an angle-bracketed one. The destination may start or end on another line of the paragraph.
    """
    found: list[str] = []
    for opened in _LINK_OPEN_RE.finditer(text):
        start = opened.end()
        stop = _destination_stop(text, start)
        if stop is not None and _LINK_END_RE.match(text, stop):
            found.append(text[start:stop])
    return found


def _join_label_lines(lines: list[str]) -> list[str]:
    """``lines`` with each reference label alone on its line joined to the line after it, so that a
    definition whose destination is on the next line reads as one line for the splitter."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _LABEL_ONLY_RE.match(lines[i]) and i + 1 < len(lines):
            out.append(f"{lines[i]} {lines[i + 1]}")
            i += 2
        else:
            out.append(lines[i])
            i += 1
    return out


def link_pieces(lines: list[str]) -> tuple[list[str], list[str]]:
    """The link destinations and the code-span texts of ``lines`` (outside fences), as written.

    A code span is matched by its run of backticks, across the lines of its paragraph, and its text is
    taken whole. Its text and the escaped brackets are then blanked. Inline links are read from the
    paragraph (parentheses up to 32 deep, a destination over lines), and reference definitions by the
    markdown splitter of L0 (``dokimi.repos.l0.markdown.split``), line by line. The splitter's inline
    reader is not used: it reads one level of parentheses and no escapes, so it would disagree with this one.
    """
    targets: dict[str, None] = {}
    spans: dict[str, None] = {}
    for paragraph in _paragraphs(lines):
        for m in CODE_SPAN_RE.finditer(paragraph):
            text = m.group(2).replace("\n", " ").strip()
            if text:
                spans[text] = None
        masked = _unlinked_brackets(CODE_SPAN_RE.sub(lambda m: " " * len(m.group()), paragraph))
        for target in _inline_destinations(masked):
            targets[target] = None
        for line in _join_label_lines(masked.split("\n")):
            if not _DEFINITION_START_RE.match(line):
                continue
            for _, target, _ in split_markdown(line).links:
                targets[target] = None
    return list(targets), list(spans)
