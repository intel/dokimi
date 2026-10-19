# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""context subcommand group: what an agent's context window held during a run."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_context_trace(args: argparse.Namespace) -> None:
    """Per-call prompt size, peak, compactions and tool-output weight of one or more traces."""
    from dokimi.context import TraceError, read_trace

    rows, failed = [], False
    for name in args.traces:
        try:
            trace = read_trace(Path(name))
        except TraceError as exc:  # report it, and still print the traces that read
            print(f"✗ {exc}", file=sys.stderr)
            failed = True
            continue
        for problem in trace.errors:
            print(f"warning: {name}: {problem}", file=sys.stderr)
        row = {"trace": name, **trace.summary(args.window)}
        if args.calls:
            row["per_call"] = trace.calls
            row["compaction_events"] = trace.compactions
        rows.append(row)

    if args.json:
        print(json.dumps(rows, indent=2))  # always a list, one entry per trace that read
    elif rows:
        _print_text(rows, args)
    if failed:
        sys.exit(1)


def _print_text(rows: list[dict], args: argparse.Namespace) -> None:
    print(_table(rows, args.window))
    for row in rows:
        heavy = ", ".join(f"{t['tool']} {t['chars']:,}" for t in row["largest_tool_results"][:3])
        if heavy:
            print(f"{row['trace']}: largest tool results (chars): {heavy}")
        if args.calls:
            series = " ".join(str(c["prompt_tokens"]) for c in row["per_call"] if c["agent"] is None)
            print(f"{row['trace']}: prompt tokens per call: {series}")


def _table(rows: list[dict], window: int | None) -> str:
    cols = [("calls", "calls"), ("first_prompt_tokens", "first"), ("peak_prompt_tokens", "peak"),
            ("growth_tokens", "growth"), ("compactions", "compact"), ("tool_calls", "tools"),
            ("failed_tool_calls", "failed"), ("repeated_tool_calls", "repeat"),
            ("tool_result_chars", "result chars")]
    if window:
        cols.insert(3, ("peak_fill", "fill"))
    head = "| trace | " + " | ".join(label for _, label in cols) + " |"
    lines = [head, "|" + "---|" * (len(cols) + 1)]
    for row in rows:
        cells = []
        for key, _ in cols:
            value = row[key]
            if value is None:
                cells.append("–")
            elif key == "peak_fill":
                cells.append(f"{value:.0%}")
            else:
                cells.append(f"{value:,}")
        trace = row["trace"].replace("|", "\\|")  # a `|` in a file name would split the cell
        lines.append(f"| {trace} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def cmd_context_consistency(args: argparse.Namespace) -> None:
    """Result stability, path stability and contract conformance of repeated runs of one task."""
    import glob

    from dokimi.context import TraceError
    from dokimi.context.consistency import (apply_outcomes, load_contract, load_outcomes, profile,
                                            read_run)

    arms: dict[str, list[str]] = {}
    for spec in args.arm or []:
        label, sep, pattern = spec.partition("=")
        if not sep or not label:
            sys.exit(f"--arm wants LABEL=GLOB, got {spec!r}")
        arms.setdefault(label, []).extend(sorted(glob.glob(pattern, recursive=True)) or [pattern])
    if args.traces:
        arms.setdefault("runs", []).extend(args.traces)
    if not arms:
        sys.exit("give traces, or --arm LABEL=GLOB")
    try:
        contract = load_contract(Path(args.contract)) if args.contract else None
        outcomes = load_outcomes(Path(args.outcomes)) if args.outcomes else None
    except (OSError, ValueError) as exc:
        sys.exit(f"✗ {exc}")

    report, failed = {}, False
    for label, names in arms.items():
        runs = []
        for name in names:
            try:
                runs.append(read_run(Path(name), args.shell))
            except TraceError as exc:
                print(f"✗ {exc}", file=sys.stderr)
                failed = True
        if outcomes is not None:
            for name in apply_outcomes(runs, outcomes):
                print(f"warning: {label}: no outcome for {name}; left unjudged", file=sys.stderr)
        if len(runs) < 2:
            print(f"warning: {label}: {len(runs)} run(s); path stability needs at least 2", file=sys.stderr)
        report[label] = profile(runs, contract, args.threshold)
        report[label]["outcome_source"] = "outcomes file" if outcomes is not None else "harness result event"

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(_consistency_text(report))
    if failed:
        sys.exit(1)


def _fmt(v, pct=False) -> str:
    if v is None:
        return "–"
    return f"{v:.0%}" if pct else f"{v:.2f}"


def _consistency_text(report: dict) -> str:
    cols = ["runs", "success", "pass^k", "mix JSD", "order dist", "action dist", "calls CV",
            "strategies", "conform"]
    lines = ["| arm | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 1)]
    for label, p in report.items():
        r, path = p["result"], p["path"]
        hats = [k for k in r if k.startswith("pass^")]
        top = hats[-1] if hats else None
        conform = p.get("contract", {}).get("conformance_rate")
        lines.append("| " + " | ".join([
            label, str(r["runs"]), _fmt(r["success_rate"], True),
            f"{_fmt(r[top], True)} ({top})" if top else "–",
            _fmt(path["mix_jsd"]), _fmt(path["order_distance"]), _fmt(path["action_distance"]),
            _fmt(path["tool_calls_cv"]), str(len(p["strategies"])), _fmt(conform, True)]) + " |")
    for label, p in report.items():
        lines.append(f"\n{label}: {p['verdict']}  (outcome from {p['outcome_source']})")
        for i, g in enumerate(p["strategies"], 1):
            shown = " → ".join(g["path"][:12]) + (" …" if len(g["path"]) > 12 else "")
            lines.append(f"  S{i} ×{len(g['runs'])} {g['verdict']} ({g['passed']}/{g['judged']}): {shown}")
        c = p.get("contract")
        if c:
            for rule, count in c["violations"].items():
                lines.append(f"  ✗ {rule}: {count}/{p['result']['runs']} runs")
            if c["passed_but_nonconforming"]:
                lines.append(f"  ! {c['passed_but_nonconforming']} passing run(s) broke the contract")
    return "\n".join(lines)


def register(sub, positive_int) -> None:
    """Add the `context` subcommand group."""
    context_p = sub.add_parser(
        "context",
        help="What an agent's context window held during a run, read from its trace. No LLM call.",
    )
    context_sub = context_p.add_subparsers(dest="context_cmd", required=True)

    trace_p = context_sub.add_parser(
        "trace",
        help="Prompt size at every model call of a Claude Code stream-json trace: what start-up "
        "loaded, how the history grew, compactions, which tool results weighed most, failed and "
        "repeated tool calls. Several traces print as one comparison table.",
    )
    trace_p.add_argument("traces", nargs="+",
                         help="`claude -p --output-format stream-json --verbose` output files")
    trace_p.add_argument("--window", type=positive_int, default=None,
                         help="Context window of the model in tokens; adds the peak's fill fraction")
    trace_p.add_argument("--calls", action="store_true",
                         help="Also give every call's token counts and every compaction event")
    trace_p.add_argument("--json", action="store_true", help="Print the summaries as JSON")
    trace_p.set_defaults(func=cmd_context_trace)

    cons_p = context_sub.add_parser(
        "consistency",
        help="Repeated runs of one task, read from their stream-json traces: result stability "
        "(success, pass@k, pass^k), path stability (tool mix JSD, order and action edit distance, "
        "resource spread), strategy groups, and conformance to an optional process contract.",
    )
    cons_p.add_argument("traces", nargs="*", help="Traces of one arm (or use --arm)")
    cons_p.add_argument("--arm", action="append", metavar="LABEL=GLOB",
                        help="An arm to compare, e.g. no-skill='runs/base/*.jsonl'; repeatable")
    cons_p.add_argument("--outcomes", metavar="JSON",
                        help="Verifier verdicts, trace -> true/false; without it a run passes "
                        "when the harness ended cleanly, which is not task success")
    cons_p.add_argument("--contract", metavar="YAML",
                        help="Process contract: named actions and rules (requires, forbids, "
                        "before, after_last, last_ok)")
    cons_p.add_argument("--threshold", type=float, default=0.34,
                        help="Max order distance of collapsed paths within one strategy (default 0.34)")
    cons_p.add_argument("--shell", choices=("split", "call"), default="split",
                        help="split (default): one step per chained shell command, shell writes "
                        "count as edits; call: one step per tool call")
    cons_p.add_argument("--json", action="store_true", help="Print the profiles as JSON")
    cons_p.set_defaults(func=cmd_context_consistency)
