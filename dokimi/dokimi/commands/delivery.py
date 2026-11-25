# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""CLI for release contracts and AX scans."""

import argparse
import math
from pathlib import Path

from dokimi import artifacts, cli_output
from dokimi.gate.delivery import evaluate
from dokimi.llm import ANSWER_PROVIDERS
from dokimi.harnesses.delivery import run
from dokimi.report.delivery import render
from dokimi.utils import InputError


def _cost_summary(observations: list[dict]) -> dict:
    calls = 0
    priced = 0
    spent = 0.0
    for row in observations:
        if not row.get("model_calls"):
            continue
        calls += 1
        value = row.get("cost_usd")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            continue
        try:
            total = spent + value
        except OverflowError:
            continue
        if math.isfinite(total):
            spent = total
            priced += 1
    return {"calls": calls, "spent_usd": spent, "priced": priced == calls if calls else None}


def command(args: argparse.Namespace) -> None:
    if args.mode == "report":
        data = artifacts.load_artifact("delivery", args.result)
        if data["mode"] == "release":
            data["summary"] = evaluate(data["required"], data["identity"], data["observations"])
    else:
        try:
            data = run(args)
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise InputError(f"invalid delivery inputs: {exc}") from exc
        data = artifacts.save_artifact("delivery", data, args.out)
    summary = data["summary"]
    verdict = summary.get("verdict")
    if args.json:
        cost = _cost_summary(data["observations"])
        if args.mode == "report":
            for row in data["observations"]:
                # A valid JSON exponent can decode to infinity, which JSON output cannot represent.
                value = row.get("cost_usd")
                if isinstance(value, float) and not math.isfinite(value):
                    row["cost_usd"] = None
        cli_output.emit(
            cli_output.envelope(
                args.cli_kind,
                data=data,
                run_id=data.get("run_id"),
                cost=cost,
                verdict=verdict.lower() if verdict else None,
                status="partial"
                if summary.get("partial") or verdict == "INCONCLUSIVE"
                else "complete",
            )
        )
    else:
        print(render(data), end="")
    if verdict == "FAIL":
        raise SystemExit(1)
    if verdict == "INCONCLUSIVE" or summary.get("partial"):
        raise SystemExit(3)


def register(sub: argparse._SubParsersAction) -> None:
    group = sub.add_parser("delivery", help="Evaluate a shipped product from a consumer workspace")
    leaves = group.add_subparsers(dest="mode", required=True)
    for mode, help_text in [
        ("release", "Check required delivery contracts"),
        ("scan", "Replay paired source snapshots"),
        ("report", "Read a saved delivery result"),
    ]:
        parser = leaves.add_parser(mode, help=help_text)
        parser.set_defaults(func=command, cli_kind=f"delivery {mode}", emits_cli_output=True)
        parser.add_argument("--json", action="store_true")
        if mode == "report":
            parser.add_argument("result", type=Path)
            continue
        for flag in ("manifest", "tasks", "policy" if mode == "release" else "corpus", "out"):
            parser.add_argument("--" + flag, required=True, type=Path)
        if mode == "scan":
            adapter = parser.add_mutually_exclusive_group(required=True)
            adapter.add_argument("--model", help="Pinned model for a one-turn shell consumer")
            adapter.add_argument(
                "--fixture",
                action="store_true",
                help="Use recorded responses from the task catalog",
            )
            parser.add_argument(
                "--provider", choices=ANSWER_PROVIDERS, default="openai"
            )
