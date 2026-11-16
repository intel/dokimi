# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read a Claude Code auto-memory directory into entries: the index, topic files, the archive.

Layout (https://code.claude.com/docs/en/memory): ``MEMORY.md`` is the index, every other
``*.md`` is a topic file with optional YAML frontmatter (``name``, ``description``, ``type``,
``modified``). Files under ``archive/`` are kept but not linked from the index.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from urllib.parse import unquote

import yaml

INDEX_NAME = "MEMORY.md"
ARCHIVE_DIR = "archive"
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?:(.*?)\r?\n)??---[ \t]*(?:\r?\n|\Z)", re.S)
_WS = r"[ \t\r]*(?:\n[ \t\r]*)?"  # spaces, and at most one line break: a destination may start on the next line
_MD_LINK = re.compile(r"\]\(" + _WS + r"(<[^>\n]+>|(?:\\\S|[^()\s\\]|\([^()\s]*\))+)(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?" + _WS + r"\)")
_MD_REF_DEF = re.compile(  # a definition: label, destination, then nothing or a title (which may run on to the next line)
    r"^ {0,3}\[(?!\^)([^\]\n]+)\]:" + _WS + r"(<[^>\n]+>|\S+)"
    r"(?:[ \t]+[\"'(](?:\\.|\\$|[^\\])*)?\s*$")
_MD_LABEL = re.compile(r"\[([^\[\]\n]+)\]")
_LABEL_ACROSS_LINES = re.compile(r"\[([^\[\]]+)\]")  # used on two joined lines: a label may wrap a line break
_EMPTY_LINK = re.compile(r"\]\(\s*(?:<>)?\s*\)")  # [t]() is an inline link with an empty destination
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")  # a reference ends in a semicolon
_MD_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)[^\n]*?(?<!`)\1(?!`)")  # a backtick run closed by one of the same length
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]+:")  # two letters or more, so a drive path like C:\\x is not a scheme
_LIST_ITEM = re.compile(r" *(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)")
_BLOCK_START = re.compile(r" {0,3}(?:#{1,6}(?:[ \t]|$)|>)")
_BACKTICK_RUN = re.compile(r"(?<!`)`+(?!`)")
_ANGLE_LINK = re.compile(r"\]\(" + _WS + r"<[^<>\n]*>(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?" + _WS + r"\)")  # the title grammar of _MD_LINK
_TITLE_CLOSED = re.compile(r"[ \t]+(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\((?:\\.|[^()\\])*\))\s*$")


class MemoryStoreError(ValueError):
    """The path is not a memory directory that can be read."""


@dataclass
class MemoryEntry:
    """One memory file. ``path`` is relative to the memory directory, with ``/`` separators."""

    path: str
    text: str
    body: str
    frontmatter: dict | None
    frontmatter_error: str | None = None
    archived: bool = False

    @property
    def name(self) -> str | None:
        value = (self.frontmatter or {}).get("name")
        return str(value) if value else None

    @property
    def type(self) -> str | None:
        """``type`` at the top level, or under ``metadata`` as some writers put it."""
        fm = self.frontmatter or {}
        meta = fm.get("metadata")
        value = fm.get("type") or (meta.get("type") if isinstance(meta, dict) else None)
        return str(value) if value else None

    @property
    def wikilinks(self) -> list[tuple[int, str]]:
        """``(line, target)`` of every ``[[target]]``, 1-based lines of the whole file."""
        return [(n, re.split(r"[|#]", m.group(1), maxsplit=1)[0].strip())
                for n, line in enumerate(self.text.split("\n"), 1)
                for m in _WIKILINK.finditer(line)]


@dataclass
class MemoryStore:
    """A memory directory: the index (``None`` when absent) and every entry under it."""

    root: Path
    index: MemoryEntry | None
    entries: list[MemoryEntry] = field(default_factory=list)

    def slugs(self) -> dict[str, MemoryEntry]:
        """Every name an entry answers to: its file stem and its ``name``, ``-`` and ``_`` folded."""
        out: dict[str, MemoryEntry] = {}
        for entry in self.entries:
            for key in (Path(entry.path).stem, entry.name):
                if key:
                    out.setdefault(slug(key), entry)
        return out

    def index_targets(self) -> list[tuple[int, str, bool]]:
        """``(line, target, checked)`` of every relative link in the index (URLs and anchors left out).

        ``checked`` is False for a definition no link uses: it names its file, but is not rendered as a link.
        """
        if self.index is None:
            return []
        lines = prose_lines(self.index.text)  # blanked to the same columns, so a label is read from the raw text
        source = self.index.text.split("\n")
        defs = _definitions(lines)
        used = _used_labels(lines, source, defs)
        found: list[tuple[int, str, bool]] = []
        text = "\n".join(lines)
        starts = [m.start() for m in re.finditer("\n", text)]  # a link may run over a line break: find its line by offset
        begin = [0, *(s + 1 for s in starts)]
        titles = _title_spans(lines, begin, defs)  # a closed title holds no link
        for m in _MD_LINK.finditer(text):
            if _has_opener(text, m.start()) and not any(lo <= m.start() < hi for lo, hi in titles):
                found.append((bisect_right(starts, m.start()) + 1, m.group(1), True))
        defined: set[str] = set()
        for k, m in sorted(defs.items()):  # the first definition of a label is the one Markdown uses
            label = _label(source[k][m.start(1):m.end(1)])
            if label not in defined:
                found.append((k + 1, m.group(2), label in used))
            defined.add(label)
        out: list[tuple[int, str, bool]] = []
        for n, raw, checked in sorted(found, key=lambda t: t[0]):
            target = unquote(_ENTITY.sub(lambda m: unescape(m.group()), _MD_ESCAPE.sub(r"\1", raw.strip("<>"))).split("#", 1)[0])
            if target and not _SCHEME.match(target) and "://" not in target:
                out.append((n, target, checked))
        return out

    def index_links(self) -> list[tuple[int, str]]:
        """``(line, target)`` of every index link that is rendered, i.e. checked: unused definitions are left out."""
        return [(n, target) for n, target, checked in self.index_targets() if checked]


def prose_lines(text: str) -> list[str]:
    """The lines of ``text`` with fenced blocks, HTML comments and code spans blanked: no link lives there."""
    return _blank_blocks(text.split("\n"))


def _blank_blocks(lines: list[str]) -> list[str]:
    """The lines with fenced and indented code, HTML comments and code spans blanked to spaces, so every column keeps its place.

    One scan, so whichever opens first holds the lines after it. A comment that opens a line (up to three
    spaces in) runs to its ``-->``, blank lines included, or to the end of the text. One opened mid-line
    needs a ``-->`` in its paragraph and ends at a blank line, a fence or the next block start.
    """
    out: list[str] = []
    fence = ""
    comment = ""  # "", or the kind of the comment the scan is inside: "block" or "inline"
    span = ""  # the backtick run of a code span that runs on over the line break
    in_list = False
    code = False  # the previous line was an indented code line
    close_ahead = _close_ahead(lines)
    for i, line in enumerate(lines):
        opener = _fence_opener(line)
        if fence:
            if _closes_fence(line, fence):
                fence = ""
            out.append(" " * len(line))
            continue
        if comment:
            rest, comment, opener = _resume_comment(line, comment, opener)
            if rest is None:
                out.append(" " * len(line))
                continue
            line = rest
        if opener:
            fence = opener
            out.append(" " * len(line))
            continue
        pos = 0
        if span:
            pos = _span_end(line, span)
            if pos < 0:
                out.append(" " * len(line))
                continue
            line = " " * pos + line[pos:]
            span = ""
        in_list, code, hidden = _indented_code(lines, i, in_list, code and not pos)
        if hidden and not pos:
            out.append(" " * len(line))
            continue
        line, comment, run = _blank_inline(line, close_ahead[i], pos, _paragraph_above(lines, out, i))
        line, span = _open_span(lines, i, line, run)
        out.append(line)
    return out


def _resume_comment(line: str, comment: str, opener: str) -> tuple[str | None, str, str]:
    """``(line, comment, opener)`` for a line met inside a comment; ``line`` is None while the comment holds all of it."""
    if comment == "inline" and _paragraph_break(line):  # a blank line, a fence or a block start ends it
        return line, "", opener
    end = line.find("-->")
    if end < 0:
        return None, comment, opener
    return " " * (end + 3) + line[end + 3:], "", ""  # the text after the close is prose, not a fence


def _code_span(line: str, pos: int) -> re.Match | None:
    """The first code span of ``line`` from ``pos`` whose opening backtick is not escaped."""
    while (m := _CODE_SPAN.search(line, pos)) is not None:
        if not _escaped(line, m.start()):
            return m
        pos = m.start() + 1
    return None


def _title_spans(lines: list[str], begin: list[int], defs: dict[int, re.Match]) -> list[tuple[int, int]]:
    """The offsets in the joined text of each definition's title, for a title closed on its line or the next."""
    out = []
    for k, d in defs.items():
        joined = d.end() > len(lines[k])  # the destination, and so the title, is on line k + 1
        rest = ("\n".join(lines[k:k + 2]) if joined else lines[k])[d.end(2):]
        if _TITLE_CLOSED.match(rest):
            out.append((begin[k] + d.end(2), begin[k] + d.end()))
    return out


def _angle_link_end(line: str, pos: int, before: str = "") -> int | None:
    """End, on ``line``, of the ``](<...>)`` link whose destination holds ``pos``: a backtick there is text, and opens no span.

    Its ``]`` must close an opener in the paragraph, which may begin on the lines above (``before``).
    """
    head = before + "\n" if before else ""
    text = head + line
    p = len(head) + pos
    i = text.rfind("](", 0, p)
    m = _ANGLE_LINK.match(text, i) if i >= 0 and _has_opener(text, i) else None
    return m.end() - len(head) if m and m.end() > p else None


def _paragraph_above(lines: list[str], out: list[str], i: int) -> str:
    """The blanked lines above ``i`` in its paragraph, joined. A raw blank line or fence ends it; a line blanked to spaces does not."""
    k = i
    while k > 0 and lines[k - 1].strip() and not _FENCE.match(lines[k - 1]):  # a fence ends a paragraph too
        k -= 1
    return "\n".join(out[k:i])


def _unpaired_run(line: str, before: str = "") -> re.Match | None:
    """The first backtick run left on ``line`` whose first backtick is not escaped and not in a ``<...>`` destination."""
    return next((m for m in _BACKTICK_RUN.finditer(line)
                 if not _escaped(line, m.start()) and _angle_link_end(line, m.start(), before) is None), None)


def _open_span(lines: list[str], i: int, line: str, run: re.Match | None) -> tuple[str, str]:
    """``(line, span)``: an unpaired backtick run that closes in a later line of its paragraph blanks the rest of ``line``."""
    if run and _span_closes_ahead(lines, i, run.group()):
        return line[:run.start()] + " " * (len(line) - run.start()), run.group()
    return line, ""


def _span_end(line: str, span: str) -> int:
    """The offset just past the backtick run ``span`` that closes a code span on ``line``, or -1."""
    closer = re.search(r"(?<!`)" + span + r"(?!`)", line)
    return closer.end() if closer else -1


def _indented_code(lines: list[str], i: int, in_list: bool, code: bool) -> tuple[bool, bool, bool]:
    """``(in_list, code, hidden)`` after line ``i``: an indented line outside a list item, after a blank line or code, is code."""
    line = lines[i]
    if not line.strip():
        return in_list, code, False
    prev_blank = i == 0 or not lines[i - 1].strip()
    if _LIST_ITEM.match(line) and (in_list or not line.startswith(("    ", "\t")) or not (prev_blank or code)):
        in_list = True
    elif line[0] not in " \t" and (prev_blank or _BLOCK_START.match(line)):
        in_list = False
    hidden = not in_list and line.startswith(("    ", "\t")) and (prev_blank or code)
    return in_list, hidden, hidden


def _paragraph_break(line: str) -> bool:
    """Whether ``line`` ends the paragraph before it: blank, a fence, a list item, a heading or a quote."""
    return not line.strip() or bool(_fence_opener(line) or _LIST_ITEM.match(line) or _BLOCK_START.match(line))


def _close_ahead(lines: list[str]) -> list[bool]:
    """For each line, whether a ``-->`` follows it in the same paragraph."""
    ahead = [False] * len(lines)
    seen = False
    for i in range(len(lines) - 1, -1, -1):
        ahead[i] = seen
        if _paragraph_break(lines[i]):
            seen = False
        elif "-->" in lines[i]:
            seen = True
    return ahead


def _span_closes_ahead(lines: list[str], i: int, run: str) -> bool:
    """Whether a backtick run of the length of ``run`` closes the code span it opens on line ``i``, within its paragraph."""
    closer = re.compile(r"(?<!`)" + run + r"(?!`)")
    for line in lines[i + 1:]:
        if _paragraph_break(line):
            return False
        if closer.search(line):
            return True
    return False


def _closes_fence(line: str, fence: str) -> bool:
    """Whether ``line`` closes the fence ``fence``: the same character, at least as long, nothing after it."""
    m = _FENCE.match(line)
    return bool(m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip())


def _blank_inline(line: str, closes_ahead: bool, pos: int = 0, before: str = "") -> tuple[str, str, re.Match | None]:
    """Blank the code spans and the comments of ``line``, left to right: whichever starts first holds the text after it.

    Returns the line, the kind of a comment it leaves open, and a backtick run left unpaired on the line (its
    span may close on a later line). A comment opened mid-line with no ``-->`` in its paragraph
    (``closes_ahead`` False) is plain text, as Markdown reads it, so the scan goes on past it.
    """
    while True:
        code = _code_span(line, pos)
        start = line.find("<!--", pos)
        if code is None and start < 0:
            return line, "", _unpaired_run(line, before)
        if code is not None and (start < 0 or code.start() < start):
            if (link := _angle_link_end(line, code.start(), before)) is not None:  # a backtick in the destination is text
                pos = link
                continue
            line = line[:code.start()] + " " * (code.end() - code.start()) + line[code.end():]
            pos = code.end()
            continue
        end = line.find("-->", start + 2)  # from the second dash: <!--> and <!---> are complete comments too
        if end >= 0:
            line = line[:start] + " " * (end + 3 - start) + line[end + 3:]
            pos = end + 3
            continue
        block = start <= 3 and not line[:start].strip()
        if block or closes_ahead:
            return line[:start] + " " * (len(line) - start), "block" if block else "inline", None
        pos = start + 4


def _used_labels(lines: list[str], source: list[str], defs: dict[int, re.Match]) -> set[str]:
    """The labels some link uses: ``[label]``, ``[label][]``, ``[text][label]`` (only ``label``). A label is read from ``source``."""
    used: set[str] = set()  # the label that opens a definition is not a use of itself
    for k, line in enumerate(lines):
        d = defs.get(k)
        spans = [m.span() for m in _MD_LINK.finditer(line)]  # a label inside a destination or title is no use
        begin = d.end() if d else 0
        if d and d.end() > len(line):  # the destination is on the next line: the label line holds no use
            begin = len(line)
        elif (above := defs.get(k - 1)) and above.end() > len(lines[k - 1]):
            begin = above.end() - len(lines[k - 1]) - 1  # the rest of that definition's line is its title
        labels = list(_MD_LABEL.finditer(line, begin))
        for j, m in enumerate(labels):
            inside = any(start <= m.start() < end for start, end in spans)
            text_of_explicit = j + 1 < len(labels) and labels[j + 1].start() == m.end()  # [text][label]: text is no use
            two = line + "\n" + lines[k + 1] if k + 1 < len(lines) else line  # a destination may start on the next line
            if not inside and not text_of_explicit and not _inline_link_at(two, m.end()):  # [t](...) with no valid destination is [t]
                used.add(_label(source[k][m.start(1):m.end(1)]))
    for k, (a, b) in enumerate(zip(lines, lines[1:], strict=False)):  # a label may wrap onto the next line
        pair = f"{a}\n{b}"
        source_pair = f"{source[k]}\n{source[k + 1]}"
        for m in _LABEL_ACROSS_LINES.finditer(pair):
            explicit = pair.startswith("[", m.end()) and not pair.startswith("[]", m.end())
            if "\n" in m.group(1) and not explicit and not _inline_link_at(pair, m.end()):
                used.add(_label(source_pair[m.start(1):m.end(1)]))
    return used


def _definitions(lines: list[str]) -> dict[int, re.Match]:
    """The reference definitions by line index. A title that opens and never closes on its line or the next is no definition."""
    out: dict[int, re.Match] = {}
    for k, line in enumerate(lines):
        m = _MD_REF_DEF.match(line) or (k + 1 < len(lines) and _MD_REF_DEF.match(line + "\n" + lines[k + 1]))
        if not m:
            continue
        joined = m.end() > len(line)  # the destination, and so the title, is on line k + 1
        title = (line + "\n" + lines[k + 1])[m.end(2):] if joined else line[m.end(2):]
        if title.strip() and not _TITLE_CLOSED.match(title):
            closer = {'"': '"', "'": "'", "(": ")"}[title.strip()[0]]
            if not _title_closes_below(lines, k + joined, closer):
                continue
        out[k] = m
    return out


def _title_closes_below(lines: list[str], k: int, closer: str) -> bool:
    """Whether the title opened on line ``k`` closes on a later line before a blank line."""
    for line in lines[k + 1:]:
        if not line.strip():
            return False
        if re.search(r"(?<!\\)" + re.escape(closer), line):
            return True
    return False


def _escaped(text: str, i: int) -> bool:
    """Whether the character at ``i`` follows an odd number of backslashes."""
    n = 0
    while i - n - 1 >= 0 and text[i - n - 1] == "\\":
        n += 1
    return n % 2 == 1


def _has_opener(text: str, close: int) -> bool:
    """Whether the ``]`` at ``close`` closes a ``[`` in its paragraph: an escaped ``\\[`` or ``\\]`` is literal text."""
    if _escaped(text, close):
        return False
    depth = 0
    for i in range(close - 1, -1, -1):
        c = text[i]
        if c == "\n" and (i == 0 or text[i - 1] == "\n"):
            return False  # a blank line ends the paragraph
        if c == "]" and not _escaped(text, i):
            depth += 1
        elif c == "[" and not _escaped(text, i):
            if depth == 0:
                return True
            depth -= 1
    return False


def _fence_opener(line: str) -> str:
    """The fence marker that ``line`` opens, or ``""``: an info string holds no backtick."""
    m = _FENCE.match(line)
    if m and not (m.group(1)[0] == "`" and "`" in line[m.end():]):
        return m.group(1)
    return ""


def _label(text: str) -> str:
    """A reference label as Markdown matches it: whitespace folded, case ignored."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _inline_link_at(line: str, end: int) -> bool:
    """Whether the ``[label]`` ending at ``end`` is followed by a valid inline destination ``(...)``."""
    return line.startswith("(", end) and (_MD_LINK.match(line, end - 1) or _EMPTY_LINK.match(line, end - 1)) is not None


def slug(name: str) -> str:
    """The key two spellings of one memory name share: ``project_foo.md`` and ``project-foo``."""
    name = name.strip()
    if name.lower().endswith(".md"):
        name = name[:-3]
    return name.replace("_", "-").lower()


def _entry(root: Path, path: Path, archived: bool) -> MemoryEntry:
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:  # one unreadable topic is a finding; an unreadable index is bad input (exit 2)
        if path == root / INDEX_NAME:
            raise MemoryStoreError(f"{path}: unreadable: {exc.strerror or exc}") from None
        return MemoryEntry(rel, "", "", None, f"unreadable: {exc.strerror or exc}", archived)
    m = _FRONTMATTER.match(text)
    if not m:
        return MemoryEntry(rel, text, text, None, archived=archived)
    body = text[m.end():]
    try:
        data = yaml.safe_load(m.group(1) or "") or {}
    except Exception as exc:  # noqa: BLE001  # any constructor error (bad date, !!float abc, deep nesting) is this file's finding
        return MemoryEntry(rel, text, body, None, f"frontmatter is not YAML: {exc.__class__.__name__}", archived)
    if not isinstance(data, dict):
        return MemoryEntry(rel, text, body, None, "frontmatter is not a mapping", archived)
    return MemoryEntry(rel, text, body, data, archived=archived)


def read_claude_memory(root: str | Path) -> MemoryStore:
    """Read ``root`` as a Claude Code memory directory; no file is written."""
    root = Path(root)
    if not root.is_dir():
        raise MemoryStoreError(f"{root}: not a directory")
    index_path = root / INDEX_NAME
    index = _entry(root, index_path, False) if index_path.is_file() else None
    entries = [_entry(root, p, False) for p in sorted(root.glob("*.md")) if p.name != INDEX_NAME and p.is_file()]
    archive = root / ARCHIVE_DIR
    if archive.is_dir():
        entries += [_entry(root, p, True) for p in sorted(archive.rglob("*.md")) if p.is_file()]
    if index is None and not entries:
        raise MemoryStoreError(f"{root}: no {INDEX_NAME} and no *.md files")
    return MemoryStore(root, index, entries)
