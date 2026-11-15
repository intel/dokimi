# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read a Claude Code auto-memory directory into entries: the index, topic files, the archive.

Layout (https://code.claude.com/docs/en/memory): ``MEMORY.md`` is the index, every other
``*.md`` is a topic file with optional YAML frontmatter (``name``, ``description``, ``type``,
``modified``). Files under ``archive/`` are kept but not linked from the index.
"""

from __future__ import annotations

import re
from urllib.parse import unquote
from dataclasses import dataclass, field
from pathlib import Path

import yaml

INDEX_NAME = "MEMORY.md"
ARCHIVE_DIR = "archive"
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?:(.*?)\r?\n)??---[ \t]*(?:\r?\n|\Z)", re.S)
_MD_LINK = re.compile(r"\]\(\s*(<[^>\n]+>|(?:\\\S|[^()\s\\]|\([^()\s]*\))+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_MD_REF_DEF = re.compile(  # a definition: label, destination, then nothing or a title (which may run on to the next line)
    r"^ {0,3}\[(?!\^)([^\]\n]+)\]:[ \t]*(<[^>\n]+>|\S+)"
    r"(?:[ \t]+[\"'(](?:\\.|\\$|[^\\])*)?\s*$")
_MD_LABEL = re.compile(r"\[([^\[\]\n]+)\]")
_LABEL_ACROSS_LINES = re.compile(r"\[([^\[\]]+)\]")  # used on two joined lines: a label may wrap a line break
_EMPTY_LINK = re.compile(r"\]\(\s*(?:<>)?\s*\)")  # [t]() is an inline link with an empty destination
_MD_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")
_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)[^\n]*?(?<!`)\1(?!`)")  # a backtick run closed by one of the same length
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


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
        used = _used_labels(lines, source)
        out: list[tuple[int, str, bool]] = []
        defined: set[str] = set()
        for n, line in enumerate(lines, 1):
            for raw, checked in _line_targets(line, source[n - 1], used, defined):
                target = unquote(_MD_ESCAPE.sub(r"\1", raw.strip("<>")).split("#", 1)[0])
                if target and "://" not in target and not target.startswith("mailto:"):
                    out.append((n, target, checked))
        return out

    def index_links(self) -> list[tuple[int, str]]:
        """``(line, target)`` of every index link that is rendered, i.e. checked: unused definitions are left out."""
        return [(n, target) for n, target, checked in self.index_targets() if checked]


def prose_lines(text: str) -> list[str]:
    """The lines of ``text`` with fenced blocks, HTML comments and code spans blanked: no link lives there."""
    return [_CODE_SPAN.sub(lambda c: " " * len(c.group(0)), line) for line in _blank_blocks(text.split("\n"))]


def _blank_blocks(lines: list[str]) -> list[str]:
    """The lines with fenced blocks and HTML comments blanked to spaces, so every column keeps its place.

    One scan, so whichever opens first holds the lines after it. A comment that opens a line (up to three
    spaces in) runs to its ``-->``, blank lines included. One opened mid-line ends at a blank line or a fence.
    """
    out: list[str] = []
    fence = ""
    comment = ""  # "", or the kind of the comment the scan is inside: "block" or "inline"
    last_close = max((i for i, text in enumerate(lines) if "-->" in text), default=-1)
    for i, line in enumerate(lines):
        opener = _fence_opener(line)
        if fence:
            if _closes_fence(line, fence):
                fence = ""
            out.append(" " * len(line))
            continue
        if comment:
            if comment == "inline" and (not line.strip() or opener):  # a blank line or a fence ends it
                comment = ""
            else:
                end = line.find("-->")
                if end < 0:
                    out.append(" " * len(line))
                    continue
                line = " " * (end + 3) + line[end + 3:]
                comment = ""
                opener = ""  # the text after the close is prose, not a fence
        if opener:
            fence = opener
            out.append(" " * len(line))
            continue
        line = _CODE_SPAN.sub(lambda c: " " * len(c.group(0)), line)  # a code span hides a comment opener too
        line, comment = _blank_comments(line, i < last_close)
        out.append(line)
    return out


def _closes_fence(line: str, fence: str) -> bool:
    """Whether ``line`` closes the fence ``fence``: the same character, at least as long, nothing after it."""
    m = _FENCE.match(line)
    return bool(m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip())


def _blank_comments(line: str, may_close: bool) -> tuple[str, str]:
    """Blank the comments that ``line`` opens and closes; returns the line and the kind of a comment it leaves open.

    An opener with no close later in the text (``may_close`` False) is plain text, as Markdown reads it.
    """
    start = line.find("<!--")
    while start >= 0:
        end = line.find("-->", start + 2)  # from the second dash: <!--> and <!---> are complete comments too
        if end < 0:
            if not may_close:
                break
            kind = "block" if start <= 3 and not line[:start].strip() else "inline"
            return line[:start] + " " * (len(line) - start), kind
        line = line[:start] + " " * (end + 3 - start) + line[end + 3:]
        start = line.find("<!--")
    return line, ""


def _used_labels(lines: list[str], source: list[str]) -> set[str]:
    """The labels some link uses: ``[label]``, ``[label][]``, ``[label]`` across a wrapped line. A label is read from ``source``."""
    used: set[str] = set()  # the label that opens a definition is not a use of itself
    for k, line in enumerate(lines):
        d = _MD_REF_DEF.match(line)
        spans = [m.span() for m in _MD_LINK.finditer(line)]  # a label inside a destination or title is no use
        for m in _MD_LABEL.finditer(line, d.end() if d else 0):
            inside = any(start <= m.start() < end for start, end in spans)
            if not inside and not _inline_link_at(line, m.end()):  # [t](...) with no valid destination is [t]
                used.add(_label(source[k][m.start(1):m.end(1)]))
    for k, (a, b) in enumerate(zip(lines, lines[1:], strict=False)):  # a label may wrap onto the next line
        pair = f"{a}\n{b}"
        source_pair = f"{source[k]}\n{source[k + 1]}"
        for m in _LABEL_ACROSS_LINES.finditer(pair):
            if "\n" in m.group(1) and not _inline_link_at(pair, m.end()):
                used.add(_label(source_pair[m.start(1):m.end(1)]))
    return used


def _line_targets(line: str, source_line: str, used: set[str], defined: set[str]) -> list[tuple[str, bool]]:
    """The ``(destination, checked)`` of each link and of each first definition on ``line``."""
    found = [(m.group(1), True) for m in _MD_LINK.finditer(line)]
    for m in _MD_REF_DEF.finditer(line):  # the first definition of a label is the one Markdown uses
        label = _label(source_line[m.start(1):m.end(1)])
        if label not in defined:
            found.append((m.group(2), label in used))
        defined.add(label)
    return found


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
    except Exception as exc:  # any constructor error (bad date, !!float abc, deep nesting) is this file's finding
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
