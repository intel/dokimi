#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Check that the tree is ready to release as the given tag, and print its notes.

    python scripts/check_release.py v0.1.0              # check, exit non-zero on a problem
    python scripts/check_release.py v0.1.0 --notes      # also print the CHANGELOG section

Ready means: the tag is ``v`` + ``project.version`` of pyproject.toml,
CHANGELOG.md has a ``## [X.Y.Z]`` section with entries, and changelog.d/ holds
no fragment that ``scriv collect`` has not folded in yet. The release workflow
runs it before building; run it on the release PR first. See docs/releasing.md.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
# A release section has entries, not just a category heading.
_ENTRY = re.compile(r"^\s*[-*] \S", re.MULTILINE)


# A backtick fence's info string has no backtick: ```x``` is an inline span.
# A fence may open on a list item's own line (``- ```md``).
_ITEM = r"(?:[-*+]|\d{1,9}[.)])"
_FENCE = re.compile(rf"^ {{0,3}}(?:{_ITEM} {{1,4}})?(`{{3,}}(?=[^`]*$)|~{{3,}})")
_LIST = re.compile(rf"^ {{0,3}}{_ITEM} ")
_TICKS = re.compile(r"`+")
_THEMATIC = re.compile(r"^ {0,3}([-*_])(?: *\1){2,} *$")
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")
# A heading, a bullet, a ``1.`` item (only 1 interrupts a paragraph) or an HTML comment at the start of a line ends the paragraph a code span lives in.
_BLOCK = re.compile(rf"^(?: {0,3}(?:#{1,6}(?:\s|$)|<!--)|\s*(?:[-*+]|1[.)]) \S)")


def _interrupts(line: str) -> bool:
    """Whether a list item line may interrupt a paragraph: a bullet or item ``1`` may, ``2.`` may not."""
    return bool(re.match(r" {0,3}(?:[-*+]|1[.)]) ", line))


def _in_list(line: str, in_list: bool, para: bool) -> bool:
    """Whether the unindented ``line`` is in a list item: an item, or a lazy continuation of one."""
    if _THEMATIC.match(line):
        return False
    if _LIST.match(line) or not (para and in_list) or _BLOCK.match(line):
        return bool(_LIST.match(line))
    return True


def _closes(lines: list[str], row: int, col: int, size: int) -> bool:
    """Whether a backtick run of exactly ``size`` follows, within the paragraph."""
    for number in range(row, len(lines)):
        line = lines[number][col:] if number == row else lines[number]
        if number > row and (not line.strip() or _FENCE.match(line) or _BLOCK.match(line)):
            return False
        if any(len(run[0]) == size for run in _TICKS.finditer(line)):
            return True
        if _HEADING.match(lines[row]):   # a heading is one line: no span runs past it
            return False
    return False


def _inline(lines: list[str], row: int, in_comment: bool, span: int) -> tuple[str, bool, int]:
    """The visible text of ``lines[row]`` and the comment and code span state at its end."""
    line, text, col = lines[row], [], 0
    if span:
        text.append("x")
    while col < len(line):
        if in_comment:
            end = line.find("-->", col)
            if end < 0:
                break
            col, in_comment = end + 3, False
        elif span:
            match = next((m for m in _TICKS.finditer(line, col) if len(m[0]) == span), None)
            if match is None:
                break
            col, span = match.end(), 0
        elif line.startswith("<!--", col):
            col, in_comment = col + 4, True
        elif line[col] == "\\":
            text.append(line[col:col + 2])
            col += 2
        elif line[col] == "`":
            size = len(_TICKS.match(line, col)[0])
            span = size if _closes(lines, row, col + size, size) else 0
            text.append("x" if span else "`" * size)
            col += size
        else:
            text.append(line[col])
            col += 1
    return "".join(text), in_comment, span


def _scan(lines: list[str]) -> list[tuple[bool, str]]:
    """For each line, whether it starts outside a fence and a comment, and its visible text.

    The text has HTML comments removed and each inline code span (which may
    run over several lines) replaced by ``x``; a fence line, a line inside a
    fence and the part of a line inside a comment have none. Fence markers
    inside a comment, comment markers inside a fence or a code span, and an
    escaped ``\\<`` mean nothing; backticks inside a comment are literal.
    """
    scanned, opener, in_comment, span = [], "", False, 0
    base = 0   # the open fence's container indent: a line less indented ends the fence
    in_list, code = False, False   # in a list item; the last line was indented code
    para = False   # the last line was paragraph text a following line may lazily continue
    for row, line in enumerate(lines):
        if opener and line.strip() and len(line) - len(line.lstrip(" ")) < base:
            opener = ""   # the list item holding the fence ended
        if opener:
            scanned.append((False, ""))
            para = False
            if re.match(rf"^ {{{base},{base + 3}}}({re.escape(opener[0])}{{3,}})\s*$", line) and (
                    len(line.strip()) >= len(opener)):
                opener = ""
            continue
        fence = None if in_comment or span else _FENCE.match(line)
        if fence and para and not in_list and _LIST.match(line) and not _interrupts(line):
            fence = None   # ``2. ~~~`` inside a paragraph is text, not a list item holding a fence
        if fence:
            listed = bool(_LIST.match(line))
            opener, base, para = fence[1], (fence.start(1) if listed else 0), False
            in_list = listed or (in_list and fence.start(1) > 0)   # an indented fence stays in its item
            scanned.append((False, ""))
            continue
        if not (in_comment or span) and line.startswith(("    ", "\t")) and not in_list and (
                code or row == 0 or not lines[row - 1].strip()):
            code, para = True, False   # indented code: its comment markers and backticks are literal
            scanned.append((True, ""))
            continue
        code = False
        if line.strip() and not line.startswith(("    ", "\t")):
            in_list = _in_list(line, in_list, para)
        starts_visible = not in_comment
        text, in_comment, span = _inline(lines, row, in_comment, span)
        # a heading, a thematic break or a line of only comment is no paragraph text
        para = bool(text.strip()) and not _HEADING.match(line) and not _THEMATIC.match(line)
        scanned.append((starts_visible, text))
    return scanned


def _section(changelog: str, version: str) -> tuple[list[str], int, int, list[tuple[bool, str]]] | None:
    lines = changelog.split("\n")
    scanned = _scan(lines)
    start = re.compile(rf"^## \[{re.escape(version)}\]")
    begin = next((i for i, line in enumerate(lines) if scanned[i][0] and start.match(line)), None)
    if begin is None:
        return None
    end = next((i for i in range(begin + 1, len(lines))
                if scanned[i][0] and lines[i].startswith(("## ", "<a id="))), len(lines))
    return lines, begin, end, scanned


def release_notes(changelog: str, version: str) -> str | None:
    """The body of the ``## [version]`` section, or None if there is none.

    The section ends at the next heading or at the ``<a id=...>`` anchor scriv
    writes above each version. A heading or anchor inside a fenced code block
    or an HTML comment does not end it.
    """
    section = _section(changelog, version)
    if section is None:
        return None
    lines, begin, end, _ = section
    body = "\n".join(lines[begin + 1:end])
    return re.sub(r"\A(?:[ \t]*\n)+", "", body).rstrip()   # keep the first line's indent


def _entries(scanned: list[tuple[bool, str]]) -> bool:
    return any(_ENTRY.search(text) for visible, text in scanned if visible)


def _has_entry(notes: str) -> bool:
    """Whether the notes hold a bullet outside HTML comments and fenced code blocks."""
    return _entries(_scan(notes.split("\n")))


def _section_has_entry(changelog: str, version: str) -> bool:
    """Like ``_has_entry`` on the section's notes, but a comment opened on its heading still counts."""
    section = _section(changelog, version)
    return section is not None and _entries(section[3][section[1] + 1:section[2]])


def problems(tag: str, project: Path = PROJECT) -> tuple[list[str], str | None]:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    found = []
    if tag != f"v{version}":
        found.append(f"tag {tag!r} does not match project.version {version!r} (want v{version})")
    changelog = (project / "CHANGELOG.md").read_text(encoding="utf-8")
    notes = release_notes(changelog, version)
    if not notes or not _section_has_entry(changelog, version):
        found.append(f"CHANGELOG.md has no entries under '## [{version}]': "
                     f"run `scriv collect --version {version}`")
    pending = sorted(p.name for p in (project / "changelog.d").glob("*.md") if p.name != "README.md")
    if pending:
        found.append(f"changelog.d/ has uncollected fragments: {', '.join(pending)}")
    return found, notes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="the release tag, e.g. v0.1.0")
    parser.add_argument("--notes", action="store_true", help="print the release notes")
    args = parser.parse_args(argv)
    found, notes = problems(args.tag, PROJECT)
    for problem in found:
        print(f"::error::{problem}", file=sys.stderr)
    if found:
        return 1
    if args.notes:
        print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
