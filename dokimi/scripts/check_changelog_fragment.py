#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fail a PR that changes the dokimi package without a changelog fragment.

Every PR that touches the package (``dokimi/dokimi/**``: code and the data it
ships) adds its own ``dokimi/changelog.d/<name>.md`` (``scriv create``) instead
of editing CHANGELOG.md, so concurrent PRs never conflict on the same lines.
At release time ``scriv collect`` folds the fragments into CHANGELOG.md; see
``changelog.d/README.md``.

    python scripts/check_changelog_fragment.py --base origin/main --head HEAD

``--skip-label`` (the PR carries the ``skip-changelog`` label) bypasses the
gate, for changes with no user-visible effect.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = "dokimi/dokimi/"
FRAGMENTS = "dokimi/changelog.d/"
NOT_FRAGMENTS = {"README.md"}
# The template wraps every section in an HTML comment; scriv drops comments, so
# a fragment committed unedited contributes nothing to the changelog.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    """(status, path) pairs from ``base...head``, deletions included.

    ``--no-renames``: a file moved out of the package shows as a deletion of its
    package path, which is a user-visible change like any other.
    """
    out = subprocess.run(
        ["git", "diff", "--no-renames", "--name-status", "--diff-filter=ACDM", f"{base}...{head}"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout
    pairs = []
    for line in filter(None, out.splitlines()):
        status, path = line.split("\t", 1)
        pairs.append((status, path))
    return pairs


def needs_fragment(changed: list[tuple[str, str]]) -> bool:
    return any(path.startswith(PACKAGE) for _, path in changed)


def fragment_paths(changed: list[tuple[str, str]]) -> list[str]:
    """Added or modified ``*.md`` files directly in changelog.d/ (scriv's glob is not recursive)."""
    paths = []
    for status, path in changed:
        if status == "D" or not path.startswith(FRAGMENTS):
            continue
        name = path[len(FRAGMENTS):]
        if "/" not in name and name.endswith(".md") and name not in NOT_FRAGMENTS:
            paths.append(path)
    return paths


def has_content(text: str) -> bool:
    """True if anything but comments and headings is left: an entry, not just a category."""
    return any(
        line.strip() and not line.strip().startswith("#")
        for line in _COMMENT.sub("", text).splitlines()
    )


def has_fragment(changed: list[tuple[str, str]], root: Path = REPO) -> bool:
    return any(
        (root / p).is_file() and has_content((root / p).read_text(encoding="utf-8"))
        for p in fragment_paths(changed)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--skip-label", action="store_true",
                        help="the PR carries the skip-changelog label")
    args = parser.parse_args(argv)

    if args.skip_label:
        print("skip-changelog label present: no changelog fragment required.")
        return 0
    changed = changed_files(args.base, args.head)
    if not needs_fragment(changed):
        print(f"No changes under {PACKAGE}: no changelog fragment required.")
        return 0
    if has_fragment(changed):
        print(f"Changelog fragment found in {FRAGMENTS}.")
        return 0
    print(
        f"::error::This PR changes {PACKAGE} but adds no changelog fragment (or only the "
        "unedited template). Run `scriv create` in dokimi/, uncomment one category in the "
        f"new {FRAGMENTS}<name>.md and write the entry (see {FRAGMENTS}README.md), or ask "
        "a maintainer for the skip-changelog label if the change has no user-visible effect.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
