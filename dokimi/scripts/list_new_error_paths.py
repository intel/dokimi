#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""List the error paths and tests a PR adds, for the reviewer to trace to the contract.

``REVIEW.md`` asks that each new ``raise``, ``sys.exit``/``SystemExit`` or ``exit_code=``
names the doc, contract or issue that asks for it, and that each new test cites the doc
line it checks. This script finds those added lines in the ``--base...--head`` diff of
``*.py`` files and prints one ``path:line: kind: text`` row each; it judges nothing.

    python scripts/list_new_error_paths.py --base origin/main --head HEAD

Exits 0 after the listing (also when it is empty), 2 when git cannot diff.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_KINDS = (
    ("raise", re.compile(r"(?:^|:)\s*raise\b")),
    ("exit", re.compile(r"\bsys\.exit\(|\bSystemExit\(|\bexit_code\s*=\s*[1-9]")),
    ("test", re.compile(r"^\s*(?:async\s+)?def\s+test_\w*\s*\(")),
)


def added_sites(diff: str) -> list[tuple[str, int, str, str]]:
    """``(path, line, kind, text)`` for each added line of a ``-U0`` diff that matches a kind."""
    sites: list[tuple[str, int, str, str]] = []
    path, line, prev = "", 0, ""
    for row in diff.splitlines():
        # An added line `++ x` is the row `+++ x`: a header only follows a `--- ` row.
        if row.startswith("+++ ") and prev.startswith("--- "):
            path = row[6:] if row.startswith("+++ b/") else ""
        elif m := _HUNK.match(row):
            line = int(m.group(1))
        elif row.startswith("+") and path.endswith(".py"):
            text = row[1:]
            kind = next((k for k, rx in _KINDS if rx.search(text)), None)
            if kind:
                sites.append((path, line, kind, text.strip()))
            line += 1
        prev = row
    return sites


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    args = parser.parse_args(argv)
    result = subprocess.run(
        ["git", "-c", "core.quotePath=false", "diff", "-U0", "--no-color",
         f"{args.base}...{args.head}", "--", "*.py"],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)
        return 2
    sites = added_sites(result.stdout)
    for path, line, kind, text in sites:
        print(f"{path}:{line}: {kind}: {text}")
    print(f"{len(sites)} new error path(s) and test(s); each should name its doc, contract or issue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
