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
_FRONTMATTER = re.compile(r"\A---[ \t]*\r?\n(?:(.*?)\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.S)
_MD_LINK = re.compile(r"\]\(\s*(<[^>\n]+>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
_WIKILINK = re.compile(r"\[\[([^\]\n]+)\]\]")


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

    def index_links(self) -> list[tuple[int, str]]:
        """``(line, target)`` of every relative markdown link in the index (URLs and anchors left out)."""
        if self.index is None:
            return []
        out = []
        for n, line in enumerate(self.index.text.split("\n"), 1):
            for m in _MD_LINK.finditer(line):
                target = unquote(m.group(1).strip("<>").split("#", 1)[0])
                if target and "://" not in target and not target.startswith("mailto:"):
                    out.append((n, target))
        return out


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
    except OSError as exc:  # one unreadable file is a finding, not the end of the run
        return MemoryEntry(rel, "", "", None, f"unreadable: {exc.strerror or exc}", archived)
    m = _FRONTMATTER.match(text)
    if not m:
        return MemoryEntry(rel, text, text, None, archived=archived)
    body = text[m.end():]
    try:
        data = yaml.safe_load(m.group(1) or "") or {}
    except yaml.YAMLError as exc:
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
