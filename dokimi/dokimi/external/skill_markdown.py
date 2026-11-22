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
# A scheme with a dot and only digits after it (``notes.md:42``) is a file and a line; ``tel:123`` is a scheme.
_SCHEME_RE = re.compile(r"<?(?:[A-Za-z][A-Za-z0-9+-]+:|[A-Za-z][A-Za-z0-9+.-]*\.[A-Za-z0-9+.-]*:(?!\d+(?!\w)))")
_REFERENCE_RE = re.compile(r"\]\[([^\]]+)\]")
# A definition's title may start on this line and end on a later one, so its closing quote is optional here.
_DEFINITION_TITLE = r"""(?:\s+(?:"[^"]*"?|'[^']*'?|\([^()]*\)?))?"""
_DEFINITION_RE = re.compile(rf"^ {{0,3}}\[(?!\^)([^\]]+)\]:\s*(<[^<>\n]*>|\S+){_DEFINITION_TITLE}\s*$")
# A shortcut or collapsed reference: a link only for the nesting check (_external_labels).
_SHORT_REFERENCE_RE = re.compile(r"(?<!\])\[([^\[\]]+)\](?:\[\])?(?![(\[])")
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


def _label_start(masked: str, close: int) -> int | None:
    """The index of the ``[`` that opens the label closed by the ``]`` at ``close``, or None."""
    depth = 0
    for i in range(close, -1, -1):
        if masked[i] == "]":
            depth += 1
        elif masked[i] == "[":
            depth -= 1
            if depth == 0:
                return i
    return None


def _reference_definitions(masked: str, found: dict[str, bool]) -> None:
    """Add to ``found`` the reference definitions in ``masked``: label (case-folded, spaces collapsed) -> whether the
    destination has a URL scheme. The first definition of a label decides, as in CommonMark."""
    for line in _join_label_lines(masked.split("\n")):
        m = _DEFINITION_RE.match(line)
        if m:
            found.setdefault(" ".join(m.group(1).casefold().split()), bool(_SCHEME_RE.match(m.group(2))))


def _link_closes(masked: str, definitions: dict[str, bool]) -> list[tuple[int, bool]]:
    """The ``(index of "]", destination is a URL)`` of each inline, reference, collapsed and shortcut link in ``masked``."""
    closes: list[tuple[int, bool]] = []  # (index of "]", destination is a URL) of each inline or reference link
    for opened in _LINK_OPEN_RE.finditer(masked):
        stop = _destination_stop(masked, opened.end())
        if stop is not None and _LINK_END_RE.match(masked, stop):
            closes.append((opened.start(), bool(_SCHEME_RE.match(masked, opened.end()))))
    for ref in _REFERENCE_RE.finditer(masked):
        start = _label_start(masked, ref.start())
        if start is None:
            continue
        key = " ".join(ref.group(1).casefold().split())
        if key in definitions:  # an undefined reference is plain text, not a link
            closes.append((ref.start(), definitions[key]))
    for short in _SHORT_REFERENCE_RE.finditer(masked):
        if " ".join(short.group(1).casefold().split()) in definitions:
            closes.append((short.start(1) + len(short.group(1)), False))  # a link, never a label read here
    return closes


def _external_labels(masked: str, definitions: dict[str, bool]) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each link label in ``masked`` whose destination has a URL scheme (``https:``, ``mailto:``).

    The destination is inline, or a reference whose first definition in ``definitions`` has one. A code span in such a
    label is the link's text, not a file of the skill. A label that holds another link (an image is fine) is
    no link, as links do not nest. ``masked`` has its code spans blanked to the same length, so offsets match.
    A collapsed (``[a][]``) or shortcut reference is not read as a label (its label is its key, blanked when it holds
    a code span), but one with a definition inside a label counts as a link there."""
    closes = _link_closes(masked, definitions)
    found: list[tuple[int, int]] = []
    starts = {close: _label_start(masked, close) for close, _ in closes}
    for close, is_url in closes:
        start = starts[close]
        if start is None or not is_url:
            continue
        nested = any(
            (inner := starts[c]) is not None and start < inner and c < close and masked[inner - 1 : inner] != "!"
            for c, _ in closes
        )
        if not nested:
            found.append((start, close))
    return found


def link_pieces(lines: list[str]) -> tuple[list[str], list[str]]:
    """The link destinations and the code-span texts of ``lines`` (outside fences), as written.

    A code span is matched by its run of backticks, across the lines of its paragraph, and its text is
    taken whole, except one in the label of a link with a URL destination (``[`examples/a.py`](https://...)``, or a reference to a definition with one),
    which is the link's text. Its text and the escaped brackets are then blanked. Inline links are read from the
    paragraph (parentheses up to 32 deep, a destination over lines), and reference definitions by the
    markdown splitter of L0 (``dokimi.repos.l0.markdown.split``), line by line. The splitter's inline
    reader is not used: it reads one level of parentheses and no escapes, so it would disagree with this one.
    """
    targets: dict[str, None] = {}
    spans: dict[str, None] = {}
    paragraphs = _paragraphs(lines)
    maskeds = [_unlinked_brackets(CODE_SPAN_RE.sub(lambda m: " " * len(m.group()), p)) for p in paragraphs]
    definitions: dict[str, bool] = {}
    for masked in maskeds:
        _reference_definitions(masked, definitions)
    for paragraph, masked in zip(paragraphs, maskeds, strict=True):
        labels = _external_labels(masked, definitions)
        for m in CODE_SPAN_RE.finditer(paragraph):
            text = m.group(2).replace("\n", " ").strip()
            if text and not any(a <= m.start() < b for a, b in labels):
                spans[text] = None
        for target in _inline_destinations(masked):
            targets[target] = None
        for line in _join_label_lines(masked.split("\n")):
            if not _DEFINITION_START_RE.match(line):
                continue
            for _, target, _ in split_markdown(line).links:
                targets[target] = None
    return list(targets), list(spans)
