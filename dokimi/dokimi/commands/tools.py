# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``tools`` subcommands: how a model chooses among the tools it is offered."""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

from dokimi.defaults import DEFAULT_ANSWER_PROVIDER
from dokimi.exit_codes import EXIT_USAGE, usage_exit

PROVIDERS = ["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"]


class CutBeforeToolCall(RuntimeError):
    """A reply cut at ``max_tokens`` before any tool call: billed in full, so it carries its usage."""

    def __init__(self, message: str, usage: dict):
        super().__init__(message)
        self.usage = usage


def _first_turn_caller(model: str, provider: str, max_tokens: int):
    """One ``tool_choice="auto"`` call per item; returns the first turn's tool names, text, usage.

    A reply cut at ``max_tokens`` before any tool call is an error; one cut after a call keeps
    that call (only its name is scored) and is flagged ``truncated``.
    """
    from dokimi.llm import _TRUNCATED, _extract_usage, chat_completion

    def call(item: dict) -> dict:
        t0 = time.time()
        resp = chat_completion(item["messages"], model=model, provider=provider, tools=item["tools"],
                               tool_choice="auto", max_tokens=max_tokens)
        usage = _extract_usage(resp, model, provider, latency_sec=time.time() - t0).as_metrics_dict()
        try:
            msg = resp.choices[0].message
            calls = getattr(msg, "tool_calls", None) or []
            # an empty or missing name is kept as "": a call was made, to no tool on offer
            names = [getattr(getattr(tc, "function", None), "name", None) or "" for tc in calls]
            truncated = getattr(resp.choices[0], "finish_reason", None) in _TRUNCATED
            if not calls and truncated:
                raise CutBeforeToolCall(f"reply cut at max_tokens={max_tokens} before any tool call", usage)
            return {"called": names, "text": getattr(msg, "content", None) or "",
                    "usage": usage, "truncated": truncated}
        except CutBeforeToolCall:
            raise
        except Exception as exc:
            exc.usage = usage  # type: ignore[attr-defined]
            raise

    return call


def _positive_usd(value: str) -> float:
    usd = float(value)
    if not math.isfinite(usd) or usd <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {value}")
    return usd


def _print_summary(summary: dict) -> None:
    def est(e):
        if not e or e.get("mean") is None:
            return "n/a"
        lo, hi = e["ci95"]
        return f"{e['mean']:+.2f} [{lo:+.2f}, {hi:+.2f}] (groups={e['n_groups']})"

    print(f"items={summary['n_items']} calls={summary['n_calls']} errors={summary['n_errors']}"
          f" truncated={summary['n_truncated']}")
    print(f"first-listed twin share (baseline): {est(summary['first_twin_share_baseline'])}")
    for f, e in summary["factor_effects"].items():
        print(f"  {f:13s} {est(e)}")
    po = summary["pair_outcomes"]
    print(f"pair items: both twins {po['both_twins']}, distractor {po['distractor']}, "
          f"unmatched name {po['unmatched']}, no call {po['none']}")
    if summary["control_over_call"]:
        print("controls, any tool called: " + ", ".join(f"{k} {v}" for k, v in summary["control_over_call"].items()))
    if summary["same_choice_rate"] is not None:
        print(f"same choice on every rep: {summary['same_choice_rate']} (modal share {summary['modal_share']})")


def _print_skipped(skipped: dict, n_groups: int) -> None:
    for factor, lacking in skipped.items():
        ids = [gid if len(roles) == 2 else f"{gid} twin {roles[0]}" for gid, roles in lacking.items()]
        print(f"  {factor}: skipped for {len(ids)} of {n_groups} groups ({', '.join(ids)})")


def _write_items(path: str, items: list) -> None:
    """Write the probe items to ``path``, or exit 2 naming it when it cannot be written."""
    try:
        Path(path).write_text(json.dumps(items, indent=1), encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot write --items-out {path}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from None


def _save_result(data: dict, path: str) -> None:
    """Save the tool_preference artifact to ``path``. An unwritable path exits 2 naming it; a payload
    the schema rejects exits 1."""
    from dokimi.artifacts import ArtifactValidationError, save_artifact

    try:
        save_artifact("tool_preference", data, path)
    except ArtifactValidationError as exc:  # the path was writable; the payload failed the schema
        print(f"Error: tool_preference artifact rejected: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError) as exc:  # unwritable path, including an empty one (pathlib ValueError)
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from None


def _check_out_json_writable(path: str) -> None:
    """Exit 2 before any probe call when a save to ``path`` would fail, probed with a temp file that is removed."""
    from dokimi.artifacts import _write_target

    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved = _write_target(target)
        if resolved.is_dir():
            raise IsADirectoryError(errno.EISDIR, "Is a directory", path)
        tmp = resolved.with_name(f"{resolved.name}.{os.getpid()}.{threading.get_ident()}.tmp")  # the save's name
        with tmp.open("xb"):
            pass
        tmp.unlink()
    except (OSError, ValueError) as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE) from None


def cmd_tools_preference(args: argparse.Namespace) -> None:
    from dokimi import tool_preference as tp

    try:
        doc = tp.load_groups(json.loads(Path(args.groups).read_text(encoding="utf-8")))
    except (OSError, RecursionError, ValueError, tp.GroupsError) as exc:
        usage_exit(f"--groups {args.groups}: {exc}")
    factors = list(dict.fromkeys(f for f in args.factors.split(",") if f)) if args.factors else list(tp.FACTORS)
    unknown = sorted(set(factors) - set(tp.FACTORS))
    if unknown:
        usage_exit(f"--factors: unknown {unknown} (known: {', '.join(tp.FACTORS)})")
    items = tp.build_items(doc, factors)
    n_pair = sum(i["kind"] == "pair" for i in items)
    print(f"{len(doc['groups'])} groups -> {len(items)} items ({n_pair} pair, {len(items) - n_pair} control)"
          f" x {args.reps} reps = {len(items) * args.reps} calls"
          + (f", stopping at ${args.max_cost_usd:g}" if args.max_cost_usd else ""))
    _print_skipped(tp.skipped_factors(doc, factors), len(doc["groups"]))
    if args.items_out:
        _write_items(args.items_out, items)
        print(f"items -> {args.items_out}")
    if args.dry_run:
        return
    if not args.model:
        usage_exit("--model is required unless --dry-run")
    _check_out_json_writable(args.out_json)

    def progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            print(f"  {done}/{total}", file=sys.stderr, flush=True)

    results = tp.run_probe(items, _first_turn_caller(args.model, args.provider, args.max_tokens),
                           reps=args.reps, concurrency=args.concurrency, on_progress=progress,
                           max_cost_usd=args.max_cost_usd)
    data = tp.payload(doc, items, results, model=args.model, provider=args.provider,
                      reps=args.reps, factors=factors, max_tokens=args.max_tokens,
                      max_cost_usd=args.max_cost_usd)
    _save_result(data, args.out_json)
    _print_summary(data["summary"])
    u = data["usage"]
    print(f"cost: {u['cost_usd']} USD over {u['n_calls']} calls ({u['cost_known_calls']} priced)")
    print(f"-> {args.out_json}")
    if args.max_cost_usd and u["cost_known_calls"] < u["n_calls"] and data["summary"]["n_skipped"] == 0:
        print(f"Warning: {u['n_calls'] - u['cost_known_calls']} call(s) had no price and did not count "
              f"toward --max-cost-usd", file=sys.stderr)
    if data["summary"]["n_calls"] and data["summary"]["n_errors"] == data["summary"]["n_calls"]:
        first = next(r["error"] for r in results if r["error"])
        print(f"Error: all {len(results)} call(s) failed ({first}); see results[].error in {args.out_json}",
              file=sys.stderr)
        raise SystemExit(2)  # the documented "run incomplete" exit (docs/quickstart.md), not a usage refusal
    if data["summary"]["n_skipped"]:
        why = (f"--max-cost-usd {args.max_cost_usd:g} reached"
               if u["cost_known_calls"] == u["n_calls"]
               else "a call came back with no price, so --max-cost-usd cannot be enforced")
        print(f"Error: {why}; {data['summary']['n_skipped']} of {len(results)} call(s) not run, "
              f"the summary covers the rest. See {args.out_json}", file=sys.stderr)
        raise SystemExit(2)  # the documented "run incomplete" exit (docs/quickstart.md), not a usage refusal


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
    pref_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=PROVIDERS)
    pref_p.add_argument("--factors", default=None,
                        help="Comma list of factors (default: vendor_name,assertive,social_proof,long_desc,exclusion)")
    pref_p.add_argument("--reps", type=positive_int, default=3,
                        help="Calls per item at the provider's default sampling (default: 3)")
    pref_p.add_argument("--max-tokens", type=positive_int, default=2048, dest="max_tokens",
                        help="Per call; reasoning models spend part of it before the tool call (default: 2048)")
    pref_p.add_argument("--concurrency", type=positive_int, default=8)
    pref_p.add_argument("--max-cost-usd", type=_positive_usd, default=None, dest="max_cost_usd",
                        help="Start no new call once the priced calls have spent this much; the calls "
                             "not started are recorded as skipped and, if there are any, the command "
                             "exits 2 (default: no cap)")
    pref_p.add_argument("--out-json", default="tool_preference.json", dest="out_json")
    pref_p.add_argument("--items-out", default=None, dest="items_out",
                        help="Also write the expanded items (messages + tools per item) here")
    pref_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="Build and count the items; call nothing")
    pref_p.set_defaults(func=cmd_tools_preference)
