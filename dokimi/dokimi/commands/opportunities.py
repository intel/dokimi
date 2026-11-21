# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``report opportunities``: rank stored findings into a queue of fixes an agent can pick up.

Reads novelty artifacts, repo_static outputs and llms.txt files (detected by content) and
prints a markdown table or an ``opportunities.v1`` document. No model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dokimi import llms_txt, opportunities
from dokimi.utils import InputError


def _read(path: str) -> tuple[str, list[opportunities.Opportunity]]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise InputError(f"{path}: {e}") from e
    try:
        data = json.loads(text)
    except ValueError:
        return "llms_txt", opportunities.from_llms_txt(llms_txt.check(text), target=path)
    if isinstance(data, dict) and data.get("schema") == "repo_static.v1":
        return "repo_static", opportunities.from_repo_static(data, target=path)
    if isinstance(data, dict) and isinstance(data.get("paired"), dict):
        return "novelty", opportunities.from_novelty(data)
    raise InputError(f"{path}: not a novelty artifact, a repo_static.v1 output or an llms.txt file")


def build(paths: list[str]) -> dict:
    """The ``opportunities.v1`` payload (without its envelope) for these input files."""
    inputs, items = [], []
    for path in paths:
        kind, found = _read(path)
        inputs.append({"path": path, "kind": kind})
        items.extend(found)
    return {"inputs": inputs, "items": opportunities.rank(items)}


def cmd_report_opportunities(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact, stamp, validate_artifact

    doc = build(args.inputs)
    if args.format == "json":
        doc = stamp("opportunities", doc)
        validate_artifact("opportunities", doc)
        text = json.dumps(doc, indent=2) + "\n"
    else:
        text = opportunities.render_markdown(doc["items"], top=args.top)
    if args.out and args.format == "json":
        save_artifact("opportunities", doc, args.out)
    elif args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def register(report_sub, positive_int) -> None:
    p = report_sub.add_parser(
        "opportunities",
        help="Rank novelty, repo_static and llms.txt findings into a queue of fixes (no model calls)")
    p.add_argument("inputs", nargs="+", metavar="PATH",
                   help="novelty artifact JSON, repo_static.v1 JSON, or an llms.txt file")
    p.add_argument("--format", default="markdown", choices=["markdown", "json"],
                   help="markdown table (PR comment, terminal) or an opportunities.v1 document")
    p.add_argument("--top", type=positive_int, default=10, help="Rows in the markdown table (default 10)")
    p.add_argument("--out", default=None, help="Write to this file instead of stdout")
    p.set_defaults(func=cmd_report_opportunities)
