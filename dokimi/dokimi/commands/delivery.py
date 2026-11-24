# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""CLI for release contracts and AX scans."""

import argparse
import json
from pathlib import Path

from dokimi import artifacts, cli_output
from dokimi.gate.delivery import evaluate
from dokimi.harnesses.delivery import run
from dokimi.utils import InputError


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
        calls = [row for row in data["observations"] if row.get("model_calls")]
        costs = [row.get("cost_usd") for row in calls]
        cost = {
            "calls": len(calls),
            "spent_usd": sum(c for c in costs if c is not None),
            "priced": all(c is not None for c in costs) if calls else None,
        }
        cli_output.emit(
            cli_output.envelope(
                args.cli_kind,
                data=data,
                run_id=data["run_id"],
                cost=cost,
                verdict=verdict.lower() if verdict else None,
                status="partial"
                if summary.get("partial") or verdict == "INCONCLUSIVE"
                else "complete",
            )
        )
    else:
        print(f"{data['mode']} | {data['identity']['profile']} | {data['date']}")
        print(json.dumps(summary, indent=2))
        for row in data["observations"]:
            print(
                f"{row['task']} {row['arm']} #{row['repeat']}: {row['outcome']} | {row.get('owner', '')}"
            )
            if row.get("reason"):
                print(row["reason"])
        print(data["reproduce"])
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
                "--provider", choices=("openai", "anthropic", "claude-cli"), default="openai"
            )
