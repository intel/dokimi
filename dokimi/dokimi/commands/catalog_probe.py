# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog trigger-probe``: run a trigger set through real Claude Code, one fresh run per query."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def cmd_catalog_trigger_probe(args: argparse.Namespace) -> None:
    from dokimi.eval.arm_summary import summarize_skill_selection
    from dokimi.harnesses import trigger_probe as tp
    from dokimi.skills import discover_skills
    from dokimi.utils import InputError

    catalog = Path(args.catalog)
    if not catalog.is_dir():
        raise InputError(f"--catalog is not a directory: {catalog}")
    try:
        names = {s.name for s in discover_skills(catalog)}
    except (OSError, ValueError) as exc:
        raise InputError(f"--catalog: {exc}") from exc
    if args.target and args.target not in names:
        raise InputError(f"--target {args.target!r} is not a skill in --catalog "
                         f"(have: {', '.join(sorted(names))})")
    # Checked before any paid run, so a bad --out costs nothing.
    out_path = Path(args.out)
    partial = out_path.with_name(out_path.name + ".partial")
    if out_path.is_dir() or not out_path.parent.is_dir() or not os.access(out_path.parent, os.W_OK):
        raise InputError(f"--out: not a writable file path: {out_path}")
    if not args.timeout > 0:
        raise InputError(f"--timeout must be > 0, got {args.timeout}")
    # Absolute: each run's cwd is a scratch dir, where a relative path names nothing.
    claude = shutil.which(args.claude)
    if claude is None:
        raise SystemExit(f"{args.claude!r} is not on PATH")
    queries = _labelled_queries(args, names)
    cfg = tp.ProbeConfig(model=args.model, max_turns=args.max_turns, timeout_sec=args.timeout,
                         stock=not args.sealed, listing_budget_fraction=args.listing_budget_fraction,
                         claude=os.path.abspath(claude))

    done: list = []
    lock = threading.Lock()

    def one(q):
        rec = tp.probe_trigger_set([q], catalog, cfg, trials=args.trials)[0]
        with lock:  # what has run so far survives a later failure or a kill
            done.append(rec)
            _write(partial, {"partial": True, "answers": done})
        print(f"{q.id}: " + ", ".join(
            f"{a}={'+'.join(v['tool_usage']['skill']['first_turn_loaded']) or '-'}"
            + (f" ({v['error']})" if v["error"] else "") for a, v in rec["arms"].items()),
            file=sys.stderr)
        return rec

    with ThreadPoolExecutor(max(1, args.concurrency)) as pool:
        records = list(pool.map(one, queries))
    runs = [v for r in records for v in r["arms"].values()]
    listed = {s.rsplit(":", 1)[-1] for v in runs for s in v["tool_usage"]["skill"]["listed"]}
    # A skill the harness never offered (a frontmatter name that is not its directory's,
    # a nested layout) reads as a 0% recall; say so instead of scoring it.
    not_listed = sorted(names - listed) if listed else []
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
        "max_turns_hit": sum(v["metrics"].get("result_subtype") == "error_max_turns" for v in runs),
        "not_listed": not_listed,
        "answers": records,
        "skill_selection_summary": summarize_skill_selection(records),
    }
    _write(out_path, out)
    partial.unlink(missing_ok=True)
    print(json.dumps({"cost_usd": out["cost_usd"], "claude_code_version": out["claude_code_version"],
                      "max_turns_hit": out["max_turns_hit"], "not_listed": not_listed,
                      "summary": {a: {k: s[k] for k in ("recall", "false_trigger_rate", "misroute_rate", "n_failed")}
                                  for a, s in out["skill_selection_summary"].items()}}, indent=2))
    print(f"wrote {args.out}")
    if not_listed:
        raise SystemExit(f"catalog skills Claude Code did not list: {', '.join(not_listed)}")


def _labelled_queries(args: argparse.Namespace, names: set) -> list:
    """The labelled queries to run; a label naming no *names* skill is an input error."""
    from dokimi.external import trigger_set as ts
    from dokimi.utils import InputError

    try:
        data = json.loads(Path(args.trigger_set).read_text(encoding="utf-8"))
        _fmt, queries = ts.read_trigger_set(data, target=args.target)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"--trigger-set: {exc}") from exc
    queries = [q for q in queries if q.labelled][: args.limit or None]
    # As the skill-catalog: arm refuses it: a label no run can load scores as a miss.
    unknown = sorted({s for q in queries for s in q.acceptable} - names)
    if unknown:
        raise InputError(f"--trigger-set: expected_skill names no skill in --catalog: "
                         f"{', '.join(unknown)}")
    return queries


def _write(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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
                   help="claude --max-turns (default: 3); runs that hit it are counted in max_turns_hit")
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
