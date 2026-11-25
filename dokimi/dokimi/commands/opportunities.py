# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``report opportunities``: rank stored findings into a queue of fixes an agent can pick up.

Reads novelty artifacts, repo_static outputs and llms.txt files (detected by content) and
prints a markdown table or an ``opportunities.v1`` document. No model calls.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from dokimi import cli_flags, llms_txt, opportunities
from dokimi.artifacts import schema_errors
from dokimi.commands._output import write_report_text
from dokimi.utils import InputError


def _read(path: str, repository: str | None) -> tuple[str, list[opportunities.Opportunity]]:
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as e:
        raise InputError(f"{path}: {e}") from e
    try:
        data = json.loads(text)
    except ValueError as e:
        suffix = Path(path).suffix.lower()
        start = text.lstrip()
        if suffix == ".json" or start.startswith("{") or (
            suffix not in {".txt", ".md"} and start.startswith("[")
            and not re.match(r"\[[^\n\]]+\](?:\(|\[|:)", start)
        ):
            raise InputError(f"{path}: not valid JSON ({e})") from e
        return "llms_txt", opportunities.from_llms_txt(llms_txt.check(text), target=path)
    try:
        json.dumps(data, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise InputError(f"{path}: JSON contains an unpaired Unicode surrogate") from exc
    if isinstance(data, dict) and data.get("schema") == "repo_static.v1":
        if errors := schema_errors("repo_static.v1", data):
            raise InputError(f"{path}: invalid repo_static.v1: {errors[0]}")
        return "repo_static", opportunities.from_repo_static(
            data, target="", repository=repository, identity_target=repository or path)
    version = data.get("schema_version") if isinstance(data, dict) else None
    if version == "catalog_eval.v1":
        return "catalog_eval", opportunities.from_catalog_eval(data)
    if version == "judge_validity.v1":
        return "judge_validity", opportunities.from_judge_validity(data)
    if isinstance(data, dict) and isinstance(data.get("paired"), dict):
        try:
            return "novelty", opportunities.from_novelty(data)
        except InputError as exc:
            raise InputError(f"{path}: {exc}") from exc
    raise InputError(f"{path}: not a novelty, repo_static.v1, catalog_eval.v1 or "
                     "judge_validity.v1 file, nor an llms.txt file")


def build(paths: list[str], *, repository: str | None = None) -> dict:
    """The ``opportunities.v1`` payload (without its envelope) for these input files."""
    inputs, items = [], []
    for path in dict.fromkeys(paths):
        kind, found = _read(path, repository)
        inputs.append({"path": path, "kind": kind})
        items.extend(found)
    return {"inputs": inputs, "items": opportunities.rank(items)}


def cmd_report_opportunities(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact, stamp, validate_artifact

    doc = build(args.inputs, repository=args.repository)
    if args.format == "json":
        doc = stamp("opportunities", doc)
        validate_artifact("opportunities", doc)
        text = json.dumps(doc, indent=2) + "\n"
    else:
        text = opportunities.render_markdown(doc["items"], top=args.top_n)
    if not args.out:
        sys.stdout.write(text)
        return
    try:
        if args.format == "json":
            save_artifact("opportunities", doc, args.out)
        else:
            write_report_text(Path(args.out), text)
    except (OSError, UnicodeError) as exc:
        raise InputError(f"cannot write {args.out}: {exc}") from exc


def register(report_sub, positive_int) -> None:
    p = report_sub.add_parser(
        "opportunities",
        help="Rank novelty, repo, catalog, judge-validity and llms.txt findings into a queue "
             "of fixes (no model calls)")
    p.add_argument("inputs", nargs="+", metavar="PATH",
                   help="novelty, repo_static.v1, catalog_eval.v1 (catalog eval --out-dir) or "
                        "judge_validity.v1 JSON, or an llms.txt file")
    cli_flags.add(p, "repository_identity", "--repository",
                  help="One repo_static source-key identity for the supplied inputs (default: each input path)")
    p.add_argument("--format", default="markdown", choices=["markdown", "json"],
                   help="markdown table (PR comment, terminal) or an opportunities.v1 document")
    p.add_argument("--top-n", type=positive_int, default=10, help="Rows in the markdown table (default 10)")
    p.add_argument("--out", default=None, help="Write to this file instead of stdout")
    p.set_defaults(func=cmd_report_opportunities)
