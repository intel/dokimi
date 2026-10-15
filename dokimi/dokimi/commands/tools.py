# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``tools`` subcommands: how a model chooses among the tools it is offered."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROVIDERS = ["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"]


def _first_turn_caller(model: str, provider: str, max_tokens: int):
    """One ``tool_choice="auto"`` call per item; returns the first turn's tool names, text, usage."""
    from dokimi.llm import _extract_usage, chat_completion

    def call(item: dict) -> dict:
        t0 = time.time()
        resp = chat_completion(item["messages"], model=model, provider=provider, tools=item["tools"],
                               tool_choice="auto", max_tokens=max_tokens)
        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        names = [getattr(getattr(tc, "function", None), "name", None) for tc in calls]
        usage = _extract_usage(resp, model, provider, latency_sec=time.time() - t0)
        return {"called": [n for n in names if n], "text": getattr(msg, "content", None) or "",
                "usage": usage.as_metrics_dict()}

    return call


def _print_summary(summary: dict) -> None:
    def est(e):
        if not e or e.get("mean") is None:
            return "n/a"
        lo, hi = e["ci95"]
        return f"{e['mean']:+.2f} [{lo:+.2f}, {hi:+.2f}] (groups={e['n_groups']})"

    print(f"items={summary['n_items']} calls={summary['n_calls']} errors={summary['n_errors']}")
    print(f"first-listed twin share (baseline): {est(summary['first_twin_share_baseline'])}")
    for f, e in summary["factor_effects"].items():
        print(f"  {f:13s} {est(e)}")
    po = summary["pair_outcomes"]
    print(f"pair items: both twins {po['both_twins']}, distractor {po['distractor']}, no call {po['none']}")
    if summary["control_over_call"]:
        print("controls, any tool called: " + ", ".join(f"{k} {v}" for k, v in summary["control_over_call"].items()))
    if summary["same_choice_rate"] is not None:
        print(f"same choice on every rep: {summary['same_choice_rate']} (modal share {summary['modal_share']})")


def cmd_tools_preference(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi import tool_preference as tp

    try:
        doc = tp.load_groups(json.loads(Path(args.groups).read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, tp.GroupsError) as exc:
        raise SystemExit(f"--groups {args.groups}: {exc}") from exc
    factors = list(dict.fromkeys(f for f in args.factors.split(",") if f)) if args.factors else list(tp.FACTORS)
    unknown = sorted(set(factors) - set(tp.FACTORS))
    if unknown:
        raise SystemExit(f"--factors: unknown {unknown} (known: {', '.join(tp.FACTORS)})")
    items = tp.build_items(doc, factors)
    n_pair = sum(i["kind"] == "pair" for i in items)
    print(f"{len(doc['groups'])} groups -> {len(items)} items ({n_pair} pair, {len(items) - n_pair} control)"
          f" x {args.reps} reps = {len(items) * args.reps} calls")
    if args.items_out:
        Path(args.items_out).write_text(json.dumps(items, indent=1), encoding="utf-8")
        print(f"items -> {args.items_out}")
    if args.dry_run:
        return
    if not args.model:
        raise SystemExit("--model is required unless --dry-run")

    def progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            print(f"  {done}/{total}", file=sys.stderr, flush=True)

    results = tp.run_probe(items, _first_turn_caller(args.model, args.provider, args.max_tokens),
                           reps=args.reps, concurrency=args.concurrency, on_progress=progress)
    data = tp.payload(doc, items, results, model=args.model, provider=args.provider,
                      reps=args.reps, factors=factors)
    save_artifact("tool_preference", data, args.out_json)
    _print_summary(data["summary"])
    u = data["usage"]
    print(f"cost: {u['cost_usd']} USD over {u['n_calls']} calls ({u['cost_known_calls']} priced)")
    print(f"-> {args.out_json}")
    if data["summary"]["n_calls"] and data["summary"]["n_errors"] == data["summary"]["n_calls"]:
        first = next(r["error"] for r in results if r["error"])
        print(f"Error: all {len(results)} call(s) failed ({first}); see results[].error in {args.out_json}",
              file=sys.stderr)
        raise SystemExit(2)


def register(sub: argparse._SubParsersAction, positive_int) -> None:
    tools_p = sub.add_parser("tools", help="How a model chooses among the tools it is offered")
    tools_sub = tools_p.add_subparsers(dest="tools_command", required=True)

    pref_p = tools_sub.add_parser(
        "preference",
        help="Two equal tools per request: how much a name, wording or position moves the model's "
             "first-turn choice (tool_preference.v1). One call per item and rep.",
    )
    pref_p.add_argument("--groups", required=True,
                        help="tool_preference_groups.v1 JSON: requests with twin tools and distractors")
    pref_p.add_argument("--model", help="Model under test")
    pref_p.add_argument("--provider", default="openai", choices=PROVIDERS)
    pref_p.add_argument("--factors", default=None,
                        help="Comma list of factors (default: vendor_name,assertive,social_proof,long_desc,exclusion)")
    pref_p.add_argument("--reps", type=positive_int, default=3,
                        help="Calls per item at the provider's default sampling (default: 3)")
    pref_p.add_argument("--max-tokens", type=positive_int, default=2048, dest="max_tokens",
                        help="Per call; reasoning models spend part of it before the tool call (default: 2048)")
    pref_p.add_argument("--concurrency", type=positive_int, default=8)
    pref_p.add_argument("--out-json", default="tool_preference.json", dest="out_json")
    pref_p.add_argument("--items-out", default=None, dest="items_out",
                        help="Also write the expanded items (messages + tools per item) here")
    pref_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Build and count the items; call nothing")
    pref_p.set_defaults(func=cmd_tools_preference)
