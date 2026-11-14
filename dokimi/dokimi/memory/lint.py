# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Deterministic checks of a memory store: index links and size, frontmatter, links, dates.

No model call and no network. Every finding names a rule, a severity, a file and a line.
Severities: ``fail`` (the index points at nothing, or loses entries past the load limit),
``warn`` (the entry is likely wrong or stale) and ``note`` (worth a look, often fine).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
import posixpath

from dokimi.memory.store import ARCHIVE_DIR, INDEX_NAME, MemoryEntry, MemoryStore, slug

#: The four kinds Claude Code writes (https://code.claude.com/docs/en/memory).
MEMORY_TYPES = ("user", "feedback", "project", "reference")
#: Claude Code loads the first 200 lines or 25KB of MEMORY.md, whichever comes first.
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024
SEVERITIES = ("fail", "warn", "note")

_RELATIVE = re.compile(
    r"\b(today|yesterday|tomorrow|tonight|this (?:week|morning|afternoon)|next week|last week)\b"
    r"|(?<!\w)(сегодня|вчера|завтра)(?!\w)", re.I)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_CODE = re.compile(r"`[^`\n]*`")
_NOT_A_WORD_USE = re.compile(r"\.md|\(")
_STRAY_SEPARATOR = re.compile(r":\s*,|,\s*,|,\s*$")


@dataclass(frozen=True)
class Finding:
    """One problem: ``line`` is 1-based, ``None`` for the file as a whole."""

    rule: str
    severity: str
    file: str
    line: int | None
    message: str


def _index_findings(store: MemoryStore, max_lines: int, max_bytes: int) -> Iterable[Finding]:
    index = store.index
    if index is None:
        yield Finding("index-missing", "warn", INDEX_NAME, None,
                      f"no {INDEX_NAME}: nothing is loaded at session start")
        return
    lines = index.text.split("\n")
    lines = lines[:-1] if lines and not lines[-1] else lines
    size = _index_bytes(store)
    if len(lines) > max_lines or size > max_bytes:
        yield Finding("index-over-limit", "fail", INDEX_NAME, min(len(lines), max_lines + 1),
                      f"{len(lines)} lines / {size} bytes; only the first {max_lines} lines or "
                      f"{max_bytes} bytes load, so later entries are never seen at start")
    elif len(lines) > 0.8 * max_lines or size > 0.8 * max_bytes:
        yield Finding("index-near-limit", "warn", INDEX_NAME, None,
                      f"{len(lines)} lines / {size} bytes, over 80% of the load limit "
                      f"({max_lines} lines, {max_bytes} bytes)")
    for n, line in enumerate(lines, 1):
        if line.lstrip().startswith(("-", "*")) and _STRAY_SEPARATOR.search(line):
            yield Finding("index-stray-separator", "note", INDEX_NAME, n,
                          "empty list slot (':,', ',,' or a trailing ','), often left by removing a link")


def _index_bytes(store: MemoryStore) -> int:
    """The size on disk, CRLF and BOM counted: that is what the harness truncates."""
    try:
        return (store.root / INDEX_NAME).stat().st_size
    except OSError:
        return len(store.index.text.encode("utf-8")) if store.index else 0


def _link_findings(store: MemoryStore) -> Iterable[Finding]:
    linked: set[str] = set()
    root = store.root.resolve()
    for n, target in store.index_links():
        try:  # resolved, so a symlink or an absolute path is judged by where it lands
            full = (store.root / target).resolve()
        except (OSError, RuntimeError):
            full = root.parent
        if not full.is_relative_to(root):
            yield Finding("index-dead-link", "fail", INDEX_NAME, n, f"links {target}, outside the memory directory")
        elif full.is_file():
            linked.update({posixpath.normpath(target), full.relative_to(root).as_posix()})
        else:
            yield Finding("index-dead-link", "fail", INDEX_NAME, n, f"links {target}, which is not a file")
    if store.index is None:
        return
    for entry in store.entries:
        if not entry.archived and entry.path not in linked:
            yield Finding("index-orphan", "warn", entry.path, None,
                          f"not linked from {INDEX_NAME}: the agent only finds it by searching")


def _frontmatter_findings(entry: MemoryEntry) -> Iterable[Finding]:
    if entry.frontmatter_error:
        yield Finding("frontmatter-invalid", "warn", entry.path, 1, entry.frontmatter_error)
        return
    if entry.frontmatter is None:
        yield Finding("frontmatter-missing", "warn", entry.path, 1,
                      "no YAML frontmatter: no name, description or type to recall it by")
        return
    for key in ("name", "description"):
        if not entry.frontmatter.get(key):
            yield Finding("frontmatter-field", "warn", entry.path, 1, f"frontmatter has no '{key}'")
    kind = entry.type
    if kind is None:
        yield Finding("frontmatter-field", "warn", entry.path, 1, "frontmatter has no 'type'")
    elif kind not in MEMORY_TYPES:
        yield Finding("type-unknown", "warn", entry.path, 1,
                      f"type '{kind}' is not one of {', '.join(MEMORY_TYPES)}")


def _entry_findings(store: MemoryStore) -> Iterable[Finding]:
    slugs = store.slugs()
    live = [entry for entry in store.entries if not entry.archived]  # archive/ is history: not checked
    names = Counter(entry.name for entry in live if entry.name)
    for entry in live:
        yield from _frontmatter_findings(entry)
        if entry.name and names[entry.name] > 1:
            yield Finding("name-duplicate", "warn", entry.path, 1,
                          f"name '{entry.name}' is used by {names[entry.name]} files")
        for n, target in entry.wikilinks:
            if slug(target) not in slugs:
                yield Finding("wikilink-dangling", "note", entry.path, n,
                              f"[[{target}]] matches no file stem or name (fine if it marks a memory to write)")
        yield from _date_findings(entry)


def _date_findings(entry: MemoryEntry) -> Iterable[Finding]:
    offset = entry.text.count("\n", 0, len(entry.text) - len(entry.body))
    for n, line in enumerate(entry.body.split("\n"), offset + 1):
        if _DATE.search(line):  # a dated line says when "today" was
            continue
        line = _CODE.sub(lambda c: " " * len(c.group(0)), line)
        for m in _RELATIVE.finditer(line):
            if not _NOT_A_WORD_USE.match(line, m.end()):
                yield Finding("relative-date", "warn", entry.path, n,
                              f"'{m.group(0)}' without a date: it is wrong on the next read")


def lint(store: MemoryStore, *, max_lines: int = INDEX_MAX_LINES, max_bytes: int = INDEX_MAX_BYTES) -> list[Finding]:
    """Every finding of ``store``, in rule order, then file and line."""
    checks: tuple[Callable[[], Iterable[Finding]], ...] = (
        lambda: _index_findings(store, max_lines, max_bytes),
        lambda: _link_findings(store),
        lambda: _entry_findings(store),
    )
    return [f for check in checks for f in check()]


def report(store: MemoryStore, findings: list[Finding], *, max_lines: int = INDEX_MAX_LINES,
           max_bytes: int = INDEX_MAX_BYTES) -> dict:
    """The ``memory lint`` document: counts, then every finding."""
    by_sev = Counter(f.severity for f in findings)
    by_rule = Counter(f.rule for f in findings)
    index_text = store.index.text if store.index else ""
    index_lines = index_text.split("\n")
    return {
        "root": str(store.root),
        "entries": sum(not e.archived for e in store.entries),
        "archived": sum(e.archived for e in store.entries),
        "archive_dir": ARCHIVE_DIR,
        "index": {"lines": len(index_lines) - (not index_lines[-1]), "bytes": _index_bytes(store),
                  "max_lines": max_lines, "max_bytes": max_bytes},
        "types": dict(Counter(e.type or "none" for e in store.entries if not e.archived)),
        "counts": {s: by_sev.get(s, 0) for s in SEVERITIES},
        "rules": dict(sorted(by_rule.items())),
        "findings": [asdict(f) for f in findings],
    }


def render_text(doc: dict) -> str:
    """Findings one per line, ``severity rule file:line message``, then a counts line."""
    out = []
    for f in doc["findings"]:
        where = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
        out.append(f"{f['severity']:<4} {f['rule']:<22} {where}  {f['message']}")
    c, idx = doc["counts"], doc["index"]
    out.append(f"{doc['entries']} entries ({doc['archived']} archived), index {idx['lines']} lines / "
               f"{idx['bytes']} bytes: {c['fail']} fail, {c['warn']} warn, {c['note']} note")
    return "\n".join(out)
