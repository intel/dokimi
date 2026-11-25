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
from bisect import bisect_right
from collections.abc import Iterator
from typing import Optional
from urllib.parse import unquote

from dokimi.code_spans import CodeSpan, iter_code_spans, replace_code_spans
from dokimi.repos.l0.markdown import split as split_markdown


def code_spans(text: str) -> Iterator[CodeSpan]:
    """The code spans of ``text``, of any run length, across lines: a run after a backslash opens none (#1073).

    The text of a span is literal (a backslash escapes nothing in it), so no link is read inside one.
    Group 2 of a span is the text.
    """
    return iter_code_spans(text, not_after="\\")


# Brackets and the backslashes that may escape them, in order (_unlinked_brackets).
BRACKET_RE = re.compile(r"\\\\|\\[\[\]]|[\[\]]")

# An inline link target balanced in parentheses up to 32 deep (``a(b(c)).md``), in angle brackets (no line
# break inside), with an optional title; split() reads one level of parentheses only, so these are read here.
_TITLE = r"""(?:"(?:[^"\\]|\\[\s\S])*"|'(?:[^'\\]|\\[\s\S])*'|\((?:[^()\\]|\\[\s\S])*\))"""
_TARGET_TITLE = rf"(?:\s+{_TITLE})?"
_LINK_OPEN_RE = re.compile(r"\]\(\s*")
# A backslash before ASCII punctuation is an escape in a link destination (``guide\_notes.md``).
ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
# RFC 3986 scheme, dots included; a dotted one with a line or symbol after it is a file (:func:`is_uri`).
_URI_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+\-.]*:")
# After a file, a line or a symbol (``run.py:42``, ``run.py:10-20``, ``run.py:main``): read as the file's name.
_FILE_SUFFIX_RE = re.compile(r"\d+(?:[-:]\d+)?|[A-Za-z_]\w*")
_REFERENCE_RE = re.compile(r"\]\[([^\[\]]+)\]")
# A reference definition at the start of a block: label (no unescaped bracket, 999 characters at most), destination,
# and a title from that line or the next; an unclosed title is no title, and text after it no definition.
_DEFINITION_RE = re.compile(
    r"""[ ]{0,3}\[(?!\^)([^\[\]]{1,999})\]:[ \t]*\n?[ \t]*(<[^<>\n]*>|\S+)"""
    rf"""(?:(?:[ \t]+|[ \t]*\n[ \t]*){_TITLE})?[ \t]*(?:\n|$)"""
)
# A thematic break line (``---``, ``* * *``, ``___``), with its line end.
_THEMATIC_BREAK_RE = re.compile(r" {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})(?:\n|$)")
# A shortcut or collapsed reference: a link only for the nesting check (_external_labels).
_SHORT_REFERENCE_RE = re.compile(r"(?<!\])\[([^\[\]]+)\](?:\[\])?")
_FULL_REFERENCE_RE = re.compile(r"\[([^\[\]]+)\]")
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

    A thematic break ends one and is no part of any. A list item and an ATX heading are blocks of their own:
    a code span cannot run across them (CommonMark).
    """
    out: list[str] = []
    block: list[str] = []
    for line in lines + [""]:
        if _THEMATIC_BREAK_RE.match(line):  # a break ends the paragraph: no link or code span runs across it
            if block:
                out.append("\n".join(block))
                block = []
            continue
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


# What an escaped bracket is masked to: two characters that are no bracket and no space, so that a label
# ``g\\]`` stays unlike ``g`` (:func:`_unmask_brackets` gives the backslash form back).
_ESCAPED_BRACKET = {"[": "\ue000\ue001", "]": "\ue000\ue002"}
_UNMASK_BRACKET = {v: "\\" + k for k, v in _ESCAPED_BRACKET.items()}
_MASKED_BRACKET_RE = re.compile("\ue000[\ue001\ue002]")


def _unmask_brackets(text: str) -> str:
    """``text`` with the masked escaped brackets of :func:`_unlinked_brackets` written as they were."""
    return _MASKED_BRACKET_RE.sub(lambda m: _UNMASK_BRACKET[m.group()], text)


def _unlinked_brackets(text: str) -> str:
    """``text`` with the brackets that open no link masked: an escaped one (``\\[``, two marker characters, so a
    label ``g\\]`` is not ``g``), and a ``]`` with no
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
            out.append(_ESCAPED_BRACKET[token[1]])
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
    if depth or (stop == start and not (stop < len(line) and line[stop] == ")")):
        return None
    return stop  # an empty destination (``[a]()``) is a link


def _inline_destinations(text: str) -> list[str]:
    """The destinations of the inline links in ``text``, as written (``<...>`` kept, a title left out).

    A ``](`` opens a destination that runs to its matching parenthesis (up to 32 deep), or to the ``>``
    of an angle-bracketed one. The destination may start or end on another line of the paragraph.
    """
    found: list[str] = []
    for opened in _LINK_OPEN_RE.finditer(text):
        start = opened.end()
        stop = _destination_stop(text, start)
        if stop is not None and stop > start and _LINK_END_RE.match(text, stop):
            found.append(_unmask_brackets(text[start:stop]))
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


def is_uri(text: str) -> bool:
    """Whether a link destination, already decoded (``<>`` off, escapes, ``#``, ``?`` and ``%`` read), is a URL or
    another URI (``https:``, ``mailto:``, ``tel:``, ``x:``), not a file. A dotted scheme with a line or symbol after
    it (``notes.md:42``, ``notes.md:main``) is a file."""
    if "://" in text:
        return True
    scheme = _URI_SCHEME_RE.match(text)
    return bool(scheme and not ("." in scheme.group() and _FILE_SUFFIX_RE.fullmatch(text[scheme.end() :])))


def _is_uri_destination(raw: str) -> bool:
    """:func:`is_uri` of a destination as written, decoded as :func:`dokimi.external.link_files._file_ref` decodes it."""
    text = raw.strip()
    if text.startswith("<") and text.endswith(">"):
        text = text[1:-1].strip()
    text = ESCAPE_RE.sub(r"\1", text)
    text = unquote(text.split("#", 1)[0].split("?", 1)[0].strip())
    return not text.startswith("/") and is_uri(text)


_SQUARE_RE = re.compile(r"[\[\]]")


def _bracket_pairs(masked: str) -> dict[int, int]:
    """``{index of "]": index of the "[" that opens it}`` for the brackets of ``masked``, matched in one pass."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for m in _SQUARE_RE.finditer(masked):  # the brackets only: a text with few of them costs no Python step per character
        i = m.start()
        if m.group() == "[":
            stack.append(i)
        elif stack:
            pairs[i] = stack.pop()
    return pairs


def _balanced(destination: str) -> bool:
    """Whether a bare definition destination has balanced, unescaped parentheses (``<...>`` is taken whole)."""
    if destination.startswith("<"):
        return True
    depth, i = 0, 0
    while i < len(destination):
        char = destination[i]
        if char == "\\":
            i += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def _label_key(paragraph: str, m: re.Match[str]) -> str:
    """The key of the label in group 1 of ``m``, a match in the masked ``paragraph``: its text as written (a code span
    in it is not blanked, so two labels that are only code spans differ), case-folded, spaces collapsed."""
    return " ".join(paragraph[m.start(1) : m.end(1)].casefold().split())


def _reference_definitions(paragraph: str, masked: str, found: dict[str, bool]) -> None:
    """Add to ``found`` the reference definitions of ``paragraph``, masked as ``masked``: label (:func:`_label_key`)
    -> whether the destination is a URL. A definition cannot interrupt a paragraph, so only those at its start
    count, one after the other. A thematic break starts a new block, so a definition may follow one after prose.
    The first definition of a label decides, as in CommonMark."""
    pos, block_start = 0, True
    while pos < len(masked):
        if rule := _THEMATIC_BREAK_RE.match(masked, pos):  # a break (or a setext underline) ends the block before it
            pos, block_start = rule.end(), True
        elif (
            block_start
            and (m := _DEFINITION_RE.match(masked, pos))
            and _balanced(m.group(2))
            and (key := _label_key(paragraph, m))  # a blank label is no definition: the line is prose
        ):
            found.setdefault(key, _is_uri_destination(m.group(2)))
            pos = m.end()
        else:
            eol = masked.find("\n", pos)
            pos, block_start = (len(masked) if eol == -1 else eol + 1), False


def _is_image(masked: str, open_at: int) -> bool:
    """Whether the ``[`` at ``open_at`` follows an unescaped ``!``."""
    if open_at < 1 or masked[open_at - 1] != "!":
        return False
    slashes = 0
    while open_at - 2 - slashes >= 0 and masked[open_at - 2 - slashes] == "\\":
        slashes += 1
    return slashes % 2 == 0


def _link_closes(paragraph: str, masked: str, definitions: dict[str, bool]) -> tuple[list[tuple[int, bool]], str]:
    """The ``(index of "]", destination is a URL)`` of each inline, reference, collapsed and shortcut link in ``masked``,
    and ``masked`` with the destinations and titles of the inline links blanked (their text is literal). ``paragraph``
    is ``masked`` as written, for the reference keys."""
    closes: list[tuple[int, bool]] = []
    chars = list(masked)
    consumed = 0
    inline_closes: set[int] = set()  # the "]" of each inline link: a shortcut there is that link's label
    for opened in _LINK_OPEN_RE.finditer(masked):
        if opened.start() < consumed:
            continue
        stop = _destination_stop(masked, opened.end())
        end = _LINK_END_RE.match(masked, stop) if stop is not None else None
        if end is not None:
            inline_closes.add(opened.start())
            closes.append((opened.start(), _is_uri_destination(masked[opened.end() : stop])))
            consumed = end.end()
            chars[opened.end() : end.end() - 1] = " " * (end.end() - 1 - opened.end())
    literal = "".join(chars)
    pairs = _bracket_pairs(literal)
    for ref in _REFERENCE_RE.finditer(literal):
        key = _label_key(paragraph, ref)
        if ref.start() in pairs and key and key in definitions:  # an undefined reference is plain text, not a link
            closes.append((ref.start(), definitions[key]))
    for short in _SHORT_REFERENCE_RE.finditer(literal):
        if short.end() - 1 in inline_closes or _label_key(paragraph, short) not in definitions:
            continue
        full = _FULL_REFERENCE_RE.match(literal, short.end())
        if full and _label_key(paragraph, full) in definitions:
            continue  # ``[a][b]`` with ``b`` defined is the full reference, counted above; else ``[a]`` stands alone
        closes.append((short.start(1) + len(short.group(1)), False))  # a link, never a label read here
    return closes, literal


def _external_labels(paragraph: str, masked: str, definitions: dict[str, bool]) -> list[tuple[int, int]]:
    """The ``(start, end)`` of each link label in ``masked`` whose destination has a URL scheme (``https:``, ``mailto:``).

    The destination is inline, or a reference whose first definition in ``definitions`` has one. A code span in such a
    label is the link's text, not a file of the skill. A label that holds another link (an image is fine) is
    no link, as links do not nest. ``masked`` has its code spans blanked to the same length, so offsets match.
    A collapsed (``[a][]``) or shortcut reference is not read as a label (its label is its key), but one with a definition inside a label counts as a link there."""
    closes, literal = _link_closes(paragraph, masked, definitions)
    pairs = _bracket_pairs(literal)
    link_starts = sorted(pairs[c] for c, _ in closes if c in pairs and not _is_image(literal, pairs[c]))
    found: list[tuple[int, int]] = []
    for close, is_url in closes:
        start = pairs.get(close)
        if start is None or not is_url:
            continue
        inner = bisect_right(link_starts, start)  # brackets pair up, so a link that starts inside the label ends inside it
        if not (inner < len(link_starts) and link_starts[inner] < close):
            found.append((start, close))
    return found


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """``spans`` sorted, with the overlapping ones joined."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _inside(spans: list[tuple[int, int]], starts: list[int], pos: int) -> bool:
    """Whether ``pos`` is in one of the ``spans`` (:func:`_merged`), whose starts are ``starts``."""
    i = bisect_right(starts, pos) - 1
    return i >= 0 and pos < spans[i][1]


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
    maskeds = [
        _unlinked_brackets(replace_code_spans(p, lambda m: " " * len(m.group()), not_after="\\")) for p in paragraphs
    ]
    definitions: dict[str, bool] = {}
    for paragraph, masked in zip(paragraphs, maskeds, strict=True):
        _reference_definitions(paragraph, masked, definitions)
    for paragraph, masked in zip(paragraphs, maskeds, strict=True):
        labels = _merged(_external_labels(paragraph, masked, definitions))
        label_starts = [a for a, _ in labels]
        for m in code_spans(paragraph):
            text = m.group(2).replace("\n", " ").strip()
            if text and not _inside(labels, label_starts, m.start()):
                spans[text] = None
        for target in _inline_destinations(masked):
            targets[target] = None
        for line in _join_label_lines(masked.split("\n")):
            if not _DEFINITION_START_RE.match(line):
                continue
            for _, target, _ in split_markdown(line).links:  # the label stays masked: the splitter reads no escape in it
                targets[_unmask_brackets(target)] = None
    return list(targets), list(spans)
