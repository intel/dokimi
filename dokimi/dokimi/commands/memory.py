# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""memory subcommand group: an agent's memory store as a subject (#728)."""

from __future__ import annotations

import argparse
import sys

from dokimi.utils import InputError


def cmd_memory_lint(args: argparse.Namespace) -> None:
    """Deterministic checks of a memory directory; exit 1 when a finding is ``fail`` (or ``warn`` with --fail-on warn)."""
    from dokimi import cli_output
    from dokimi.memory import MemoryStoreError, read_claude_memory
    from dokimi.memory.lint import lint, render_text, report

    try:
        store = read_claude_memory(args.memory_dir)
    except (MemoryStoreError, OSError) as exc:
        raise InputError(str(exc)) from None
    doc = report(store, lint(store))
    bad = doc["counts"]["fail"] or (args.fail_on == "warn" and doc["counts"]["warn"])
    if args.json:
        cli_output.emit_leaf("memory lint", doc, status="complete")
    else:
        print(render_text(doc))
    if bad:
        sys.exit(1)


def register(sub, positive_int) -> None:
    """Add the `memory` group: check an agent's memory store. No LLM call."""
    mem_p = sub.add_parser("memory", help="An agent's memory store (Claude Code auto memory) as a subject. No LLM call.")
    mem_sub = mem_p.add_subparsers(dest="memory_cmd", required=True)
    lint_p = mem_sub.add_parser(
        "lint",
        help="Deterministic checks of a memory directory: index dead links, orphans and load limit, "
        "frontmatter fields and types, duplicate names, dangling [[wikilinks]], undated relative dates.",
    )
    lint_p.add_argument("memory_dir", help="The memory directory, e.g. ~/.claude/projects/<project>/memory")
    lint_p.add_argument("--fail-on", choices=("warn", "fail"), default="fail",
                        help="Exit 1 on a finding of this severity or worse (default fail)")
    lint_p.add_argument("--json", action="store_true", help="Print cli_output.v1 on stdout (the report under data)")
    lint_p.set_defaults(func=cmd_memory_lint, emits_cli_output=True, cli_kind="memory lint")
