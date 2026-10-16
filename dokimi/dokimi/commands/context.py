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
