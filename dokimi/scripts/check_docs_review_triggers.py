#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""List the docs pages a PR may have made stale.

A page under ``dokimi/docs/`` names the code it documents in its YAML front
matter, as globs relative to the repository root:

    ---
    depends_on:
      - dokimi/dokimi/novelty/**
      - dokimi/dokimi/commands/novelty.py
    ---
    <!-- license header -->

A PR that changes a file one of those globs matches, and does not touch the
page itself, gets the page listed: in the job summary and as a warning on the
page. It is a prompt to reread the page, not a gate: the script exits 0
whatever it finds. It exits 1 only for broken input, after naming the cause: a
page whose ``depends_on`` is malformed (``::error file=<page>::``), or 2 for a
``--base``/``--head`` git cannot diff. A glob that matches no tracked file is a stale mapping, and
``tests/test_docs_review_triggers.py`` fails on it.

Glob syntax: ``**`` matches any run of characters including ``/``, ``*`` and
``?`` stay inside one path segment, anything else is literal.

    python scripts/check_docs_review_triggers.py --base origin/main --head HEAD
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DOCS = "dokimi/docs"
# Match front matter with optional UTF-8 BOM and flexible line endings (LF or CRLF)
_FRONT_MATTER = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?\n)---[ \t]*\r?\n", re.DOTALL)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)


def glob_regex(pattern: str) -> re.Pattern[str]:
    """The regex a ``depends_on`` glob compiles to (whole-path match)."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def depends_on(text: str) -> list[str]:
    """The ``depends_on`` globs of a page's front matter; [] when it has none.

    Raises ValueError when the field is there but is not a list of strings.
    """
    match = _FRONT_MATTER.match(text)
    if not match:
        return []
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"front matter YAML is invalid: {e}") from e
    if not isinstance(meta, dict):
        raise ValueError("front matter is not a mapping")
    globs = meta.get("depends_on", [])
    if not isinstance(globs, list) or not all(isinstance(g, str) and g for g in globs):
        raise ValueError("depends_on must be a list of non-empty strings")
    return globs


def load_pages(root: Path = REPO) -> dict[str, list[str]]:
    """Repo-relative page path -> its ``depends_on`` globs, for pages that declare any."""
    pages = {}
    for path in sorted((root / DOCS).glob("*.md")):
        try:
            globs = depends_on(path.read_text(encoding="utf-8"))
            if globs:
                pages[path.relative_to(root).as_posix()] = globs
        except ValueError as e:
            raise ValueError(f"{path.relative_to(root).as_posix()}: {e}") from e
    return pages


def triggers(pages: dict[str, list[str]], changed: list[str]) -> dict[str, list[str]]:
    """Pages whose dependencies ``changed`` touches but which it leaves alone -> the hits."""
    touched = set(changed)
    flagged = {}
    for page, globs in pages.items():
        if page in touched:
            continue
        regexes = [glob_regex(g) for g in globs]
        hits = sorted(c for c in touched if any(r.match(c) for r in regexes))
        if hits:
            flagged[page] = hits
    return flagged


def stale_globs(pages: dict[str, list[str]], files: list[str]) -> list[str]:
    """``page: glob`` for every glob that matches none of ``files``."""
    return [f"{page}: {g}" for page, globs in pages.items() for g in globs
            if not any(glob_regex(g).match(f) for f in files)]


def changed_files(base: str, head: str) -> list[str]:
    """Paths changed in ``base...head``; ``--no-renames`` keeps a moved file's old path."""
    result = _git("diff", "-z", "--no-renames", "--name-only", f"{base}...{head}")
    result.check_returncode()
    return [p for p in result.stdout.split("\0") if p]


def summary(flagged: dict[str, list[str]]) -> str:
    if not flagged:
        return "No docs page depends on a file this PR changes without the PR touching the page.\n"
    rows = ["| Page | Changed dependencies |", "|---|---|"]
    rows += [f"| `{page}` | {', '.join(f'`{h}`' for h in hits)} |" for page, hits in flagged.items()]
    return ("Pages whose `depends_on` files changed while the page did not; "
            "check that they still describe the code:\n\n" + "\n".join(rows) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    try:
        changed = changed_files(args.base, args.head)
    except subprocess.CalledProcessError as exc:
        print(f"cannot diff {args.base}...{args.head}: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    try:
        pages = load_pages()
    except ValueError as exc:
        page, _, why = str(exc).partition(": ")
        print(f"::error file={page},title=Docs review::{why}")
        return 1
    flagged = triggers(pages, changed)
    text = summary(flagged)
    print(text)
    for page, hits in flagged.items():
        print(f"::warning file={page},title=Docs review::{', '.join(hits)} changed; "
              "this page documents them and was not updated")
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(step_summary, "a", encoding="utf-8") as fh:
            fh.write("## Docs review triggers\n\n" + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
