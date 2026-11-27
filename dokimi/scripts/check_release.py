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
_FENCE = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")
_CODE_SPAN = re.compile(r"`+[^`]*`+")
_COMMENT = re.compile(r"<!--.*?(?:-->|$)")


def _visible(lines: list[str]) -> list[bool]:
    """For each line, whether it starts outside a fenced code block and an HTML comment.

    Fence lines count as inside a fence. A fence marker inside a comment, and
    a comment marker inside a fence, mean nothing.
    """
    visible, opener, in_comment = [], "", False
    for line in lines:
        if opener:
            fence = _FENCE.match(line)
            visible.append(False)
            if fence and fence[1][0] == opener[0] and len(fence[1]) >= len(opener) \
                    and not line.strip().strip(opener[0]):
                opener = ""
            continue
        fence = None if in_comment else _FENCE.match(line)
        visible.append(not in_comment and not fence)
        if fence:
            opener = fence[1]
            continue
        rest = _CODE_SPAN.sub("x", line)   # a `<!--` in a code span opens nothing
        while True:
            if in_comment:
                end = rest.find("-->")
                if end < 0:
                    break
                rest, in_comment = rest[end + 3:], False
            else:
                begin = rest.find("<!--")
                if begin < 0:
                    break
                rest, in_comment = rest[begin + 4:], True
    return visible


def release_notes(changelog: str, version: str) -> str | None:
    """The body of the ``## [version]`` section, or None if there is none.

    The section ends at the next heading or at the ``<a id=...>`` anchor scriv
    writes above each version. A heading or anchor inside a fenced code block
    does not end it.
    """
    lines = changelog.split("\n")
    outside = _visible(lines)
    start = re.compile(rf"^## \[{re.escape(version)}\]")
    begin = next((i for i, line in enumerate(lines) if outside[i] and start.match(line)), None)
    if begin is None:
        return None
    end = next((i for i in range(begin + 1, len(lines))
                if outside[i] and lines[i].startswith(("## ", "<a id="))), len(lines))
    body = "\n".join(lines[begin + 1:end])
    return re.sub(r"\A(?:[ \t]*\n)+", "", body).rstrip()   # keep the first line's indent


def _has_entry(notes: str) -> bool:
    """Whether the notes hold a bullet outside HTML comments and fenced code blocks."""
    lines = notes.split("\n")
    return any(_ENTRY.search(_COMMENT.sub("", _CODE_SPAN.sub("x", line)))
               for line, out in zip(lines, _visible(lines), strict=True) if out)


def problems(tag: str, project: Path = PROJECT) -> tuple[list[str], str | None]:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    found = []
    if tag != f"v{version}":
        found.append(f"tag {tag!r} does not match project.version {version!r} (want v{version})")
    notes = release_notes((project / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    if not notes or not _has_entry(notes):
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
