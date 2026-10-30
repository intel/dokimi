# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""backends command: which coding-agent CLIs can serve as LLM providers here."""

from __future__ import annotations

import argparse
import dataclasses
import json


def cmd_backends(args: argparse.Namespace) -> None:
    """One row per CLI backend: installed, version, signed in and how. Calls no model."""
    from dokimi import llm_cli

    rows = [llm_cli.detect(name) for name in llm_cli.BACKENDS]
    if args.json:
        print(json.dumps([dataclasses.asdict(r) for r in rows], indent=2))
        return
    print("| provider | binary | version | signed in | method | models |")
    print("|---|---|---|---|---|---|")
    for r in rows:
        signed = {True: "yes", False: "no", None: "unknown"}[r.authenticated] if r.installed else "-"
        version = r.version or ("unknown" if r.installed else "not installed")
        models = ", ".join(llm_cli.BACKENDS[r.backend].aliases)
        print(f"| {r.backend} | {r.binary} | {version} | {signed} "
              f"| {r.method or '-'} | {models} |")
    for r in rows:
        if r.hint:
            print(f"{r.backend}: {r.hint}")


def register(sub, positive_int) -> None:
    """Add the `backends` command."""
    p = sub.add_parser(
        "backends",
        help="Coding-agent CLIs usable as --provider (claude-cli): installed, version, signed "
        "in. No model call.",
    )
    p.add_argument("--json", action="store_true", help="Print the rows as JSON")
    p.set_defaults(func=cmd_backends)
