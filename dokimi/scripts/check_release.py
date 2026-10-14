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


def release_notes(changelog: str, version: str) -> str | None:
    """The body of the ``## [version]`` section, or None if there is none."""
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)",
                      changelog, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else None


def problems(tag: str, project: Path = PROJECT) -> tuple[list[str], str | None]:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    version = pyproject["project"]["version"]
    found = []
    if tag != f"v{version}":
        found.append(f"tag {tag!r} does not match project.version {version!r} (want v{version})")
    notes = release_notes((project / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    if not notes:
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
    found, notes = problems(args.tag)
    for problem in found:
        print(f"::error::{problem}", file=sys.stderr)
    if found:
        return 1
    if args.notes:
        print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
