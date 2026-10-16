#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Fail a PR that changes the dokimi package without a changelog fragment.

Every PR that touches the package (``dokimi/dokimi/**``: code and the data it
ships), the data it reads from a checkout (``dokimi/data/**``) or what
``pip install`` does (``[project]`` and ``[tool.setuptools]`` of
``dokimi/pyproject.toml``: dependencies, Python versions, entry points,
package-data) adds its own ``dokimi/changelog.d/<name>.md`` (``scriv create``) instead
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
import tomllib
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
PACKAGE = "dokimi/dokimi/"
NEEDS_FRAGMENT = (PACKAGE, "dokimi/data/")
PYPROJECT = "dokimi/pyproject.toml"
FRAGMENTS = "dokimi/changelog.d/"
NOT_FRAGMENTS = {"README.md"}
# The template wraps every section in an HTML comment; scriv drops comments, so
# a fragment committed unedited contributes nothing to the changelog.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# The template's example bullet, left in under an uncommented category.
_PLACEHOLDER = re.compile(r"^\s*- \*\*Short bold summary\*\*.*?(?=^\s*$|^\s*[-#]|\Z)",
                          re.MULTILINE | re.DOTALL)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    """(status, path) pairs from ``base...head``, deletions included.

    ``--no-renames``: a file moved out of the package shows as a deletion of its
    package path, which is a user-visible change like any other.
    """
    result = _git("diff", "-z", "--no-renames", "--name-status", "--diff-filter=ACDM",
                  f"{base}...{head}")
    result.check_returncode()
    # -z: status NUL path NUL ..., and a non-ASCII path comes back as itself,
    # not quoted as "caf\303\251".
    fields = result.stdout.split("\0")
    return list(zip(fields[0:-1:2], fields[1::2], strict=True))


def _install_tables(rev: str) -> tuple[Any, Any]:
    """``[project]`` (bar the dev extra) and ``[tool.setuptools]`` of pyproject.toml at ``rev``."""
    shown = _git("show", f"{rev}:{PYPROJECT}")
    pyproject = tomllib.loads(shown.stdout) if shown.returncode == 0 else {}
    project = dict(pyproject.get("project", {}))
    project["optional-dependencies"] = {
        k: v for k, v in project.get("optional-dependencies", {}).items() if k != "dev"}
    return project, pyproject.get("tool", {}).get("setuptools")


def pyproject_install_changed(base: str, head: str) -> bool:
    """True if ``base...head`` changes what installing dokimi gives a user."""
    fork = _git("merge-base", base, head)
    fork.check_returncode()
    return _install_tables(fork.stdout.strip()) != _install_tables(head)


def needs_fragment(changed: list[tuple[str, str]], base: str | None = None,
                   head: str | None = None) -> bool:
    if any(path.startswith(NEEDS_FRAGMENT) for _, path in changed):
        return True
    return (base is not None and head is not None
            and any(path == PYPROJECT for _, path in changed)
            and pyproject_install_changed(base, head))


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
    """True if anything but comments, headings and the template's example is left:
    an entry, not just a category."""
    return any(
        line.strip() and not line.strip().startswith("#")
        for line in _PLACEHOLDER.sub("", _COMMENT.sub("", text)).splitlines()
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
    if not needs_fragment(changed, args.base, args.head):
        print("No change to the package, dokimi/data/ or what pip installs: "
              "no changelog fragment required.")
        return 0
    if has_fragment(changed):
        print(f"Changelog fragment found in {FRAGMENTS}.")
        return 0
    print(
        f"::error::This PR changes {PACKAGE}, dokimi/data/ or the install metadata of "
        f"{PYPROJECT} but adds no changelog fragment (or only the "
        "unedited template). Run `scriv create` in dokimi/, uncomment one category in the "
        f"new {FRAGMENTS}<name>.md and write the entry (see {FRAGMENTS}README.md), or ask "
        "a maintainer for the skip-changelog label if the change has no user-visible effect.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
