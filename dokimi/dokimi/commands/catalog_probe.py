# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog trigger-probe``: run a trigger set through real Claude Code, one fresh run per query."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def cmd_catalog_trigger_probe(args: argparse.Namespace) -> None:
    from dokimi.eval.arm_summary import summarize_skill_selection
    from dokimi.external import trigger_set as ts
    from dokimi.harnesses import trigger_probe as tp

    catalog = Path(args.catalog)
    if not catalog.is_dir():
        raise SystemExit(f"--catalog is not a directory: {catalog}")
    if shutil.which(args.claude) is None:
        raise SystemExit(f"{args.claude!r} is not on PATH")
    try:
        data = json.loads(Path(args.trigger_set).read_text(encoding="utf-8"))
        _fmt, queries = ts.read_trigger_set(data, target=args.target)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--trigger-set: {exc}") from exc
    queries = [q for q in queries if q.labelled][: args.limit or None]
    cfg = tp.ProbeConfig(model=args.model, max_turns=args.max_turns, timeout_sec=args.timeout,
                         stock=not args.sealed, listing_budget_fraction=args.listing_budget_fraction,
                         claude=args.claude)

    def one(q):
        rec = tp.probe_trigger_set([q], catalog, cfg, trials=args.trials)[0]
        print(f"{q.id}: " + ", ".join(
            f"{a}={'+'.join(v['tool_usage']['skill']['first_turn_loaded']) or '-'}"
            + (f" ({v['error']})" if v["error"] else "") for a, v in rec["arms"].items()),
            file=sys.stderr)
        return rec

    with ThreadPoolExecutor(max(1, args.concurrency)) as pool:
        records = list(pool.map(one, queries))
    cost = sum(v["metrics"]["cost_usd"] or 0 for r in records for v in r["arms"].values())
    out = {
        "harness": "claude-code",
        "mode": "sealed" if args.sealed else "stock",
        "model": args.model,
        "catalog": str(catalog),
        "trials": args.trials,
        "max_turns": args.max_turns,
        "listing_budget_fraction": args.listing_budget_fraction,
        "claude_code_version": next((v["tool_usage"]["skill"]["claude_code_version"] for r in records
                                     for v in r["arms"].values()
                                     if v["tool_usage"]["skill"]["claude_code_version"]), None),
        "cost_usd": round(cost, 4),
        "answers": records,
        "skill_selection_summary": summarize_skill_selection(records),
    }
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cost_usd": out["cost_usd"], "claude_code_version": out["claude_code_version"],
                      "summary": {a: {k: s[k] for k in ("recall", "false_trigger_rate", "misroute_rate", "n_failed")}
                                  for a, s in out["skill_selection_summary"].items()}}, indent=2))
    print(f"wrote {args.out}")


def register(catalog_sub, positive_int) -> None:
    """Add ``catalog trigger-probe`` to the ``catalog`` group."""
    p = catalog_sub.add_parser(
        "trigger-probe",
        help="Run each trigger-set query in a fresh, unannounced Claude Code session with the "
             "catalog installed as project skills; score which skill it invokes",
    )
    p.add_argument("--catalog", required=True, help="Directory of skills, installed as .claude/skills")
    p.add_argument("--trigger-set", required=True, dest="trigger_set",
                   help="skill-reach QuerySet, skill-creator set (with --target) or questions with expected_skill")
    p.add_argument("--target", help="Target skill of a skill-creator set")
    p.add_argument("--model", help="--model passed to claude (default: the harness default)")
    p.add_argument("--trials", type=positive_int, default=1, help="Fresh runs per query (default: 1)")
    p.add_argument("--max-turns", type=positive_int, default=3, dest="max_turns",
                   help="claude --max-turns (default: 3)")
    p.add_argument("--timeout", type=float, default=300.0, help="Seconds per run (default: 300)")
    p.add_argument("--concurrency", type=positive_int, default=4, help="Queries run at once (default: 4)")
    p.add_argument("--limit", type=positive_int, help="Only the first N labelled queries")
    p.add_argument("--listing-budget-fraction", type=float, dest="listing_budget_fraction",
                   help="skillListingBudgetFraction for the run (default: the harness's own, 0.01)")
    p.add_argument("--sealed", action="store_true",
                   help="Switch off bundled skills as the docker-claude arms do (default: stock harness)")
    p.add_argument("--claude", default="claude", help="claude executable (default: claude)")
    p.add_argument("--out", required=True, help="Output JSON path")
    p.set_defaults(func=cmd_catalog_trigger_probe)
