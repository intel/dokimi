#!/usr/bin/env python
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""List the spend controls of every paid CLI leaf (issue #940).

The input is the parser tree of ``dokimi.cli.build_parser()`` joined to
``cli_routes.cost_of``. A leaf is *capped* when it takes one of ``CAP_FLAGS``
and *previewable* when it takes one of ``PREVIEW_FLAGS``; flags are matched by
name only, so a ``--dry-run`` that only skips an output file still counts::

    python scripts/cli_spend_controls.py          # one line per paid leaf
    python scripts/cli_spend_controls.py --json   # the same, then a summary line
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dokimi import cli, cli_routes  # noqa: E402

CAP_FLAGS = frozenset({"--max-usd"})
PREVIEW_FLAGS = frozenset({"--estimate-only", "--dry-run", "--work-dry-run"})


def _leaves(parser: argparse.ArgumentParser, path: tuple[str, ...] = ()):
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield path, {o for a in parser._actions for o in a.option_strings}
        return
    for action in subs:
        for name, sub in action.choices.items():
            yield from _leaves(sub, (*path, name))


def paid_leaves() -> list[dict]:
    """One row per paid leaf: ``path``, ``capped`` and ``preview``, sorted by path."""
    rows = []
    for path, flags in _leaves(cli.build_parser()):
        if cli_routes.cost_of(path) == cli_routes.PAID:
            rows.append(
                {
                    "path": " ".join(path),
                    "capped": bool(flags & CAP_FLAGS),
                    "preview": bool(flags & PREVIEW_FLAGS),
                }
            )
    return sorted(rows, key=lambda r: r["path"])


def summary(rows: list[dict]) -> dict:
    """Counts: paid leaves, capped, uncapped, and uncapped ones with no preview either."""
    uncapped = [r for r in rows if not r["capped"]]
    return {
        "paid": len(rows),
        "capped": len(rows) - len(uncapped),
        "uncapped": len(uncapped),
        "no_control": sum(1 for r in uncapped if not r["preview"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print JSON lines")
    args = ap.parse_args(argv)
    rows = paid_leaves()
    for row in rows:
        if args.json:
            print(json.dumps(row))
        else:
            cap = "cap" if row["capped"] else "-"
            prev = "preview" if row["preview"] else "-"
            print(f"{row['path']}\t{cap}\t{prev}")
    if args.json:
        print(json.dumps(summary(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
