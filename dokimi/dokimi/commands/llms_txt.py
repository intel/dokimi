# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``docs llms-txt``: the static check of an ``llms.txt`` file (:mod:`dokimi.llms_txt`).

No network and no model call. Prints one row per finding, or ``cli_output.v1`` with ``--json``;
exits 1 when a ``fail`` finding exists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dokimi import cli_flags, llms_txt
from dokimi.utils import InputError


def resolve(path: str) -> Path:
    """The file to check: ``path`` itself, or ``llms.txt`` inside it when it is a directory."""
    target = Path(path)
    if target.is_dir():
        target = target / "llms.txt"
    if not target.is_file():
        raise InputError(f"{target}: no such file (give an llms.txt file or a directory that holds one)")
    return target


def render_text(target: str, findings: list[llms_txt.Finding]) -> str:
    """A tab-separated row per finding (line 0 means the whole file), then a one-line verdict."""
    rows = [f"{f.severity}\t{f.check}\t{target}:{f.line}\t{f.message}\t{f.fix}" for f in findings]
    fails = sum(f.severity == "fail" for f in findings)
    warns = len(findings) - fails
    verdict = "fail" if fails else "pass"
    return "\n".join([*rows, f"# {target}: {verdict} ({fails} fail, {warns} warn)"]) + "\n"


def cmd_docs_llms_txt(args: argparse.Namespace) -> None:
    target = resolve(args.path)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"{target}: {e}") from e
    findings = llms_txt.check(text, token_budget=args.token_budget)
    fails = sum(f.severity == "fail" for f in findings)
    name = target.as_posix()
    if args.json:
        from dokimi import cli_output

        verdict = "fail" if fails else "pass"
        subject = {"subject": name, "verdict": verdict,
                   "findings": [cli_output.finding(f.severity, f.check, f.message, name, f.line or None)
                                for f in findings]}
        cli_output.emit_leaf(
            "docs llms-txt",
            {"path": name, "tokens": llms_txt.estimate_tokens(text), "token_budget": args.token_budget,
             "findings": [f.as_dict() for f in findings]},
            verdict=verdict, reason=f"{fails} fail finding(s)" if fails else None,
            summary={"fail": fails, "warn": len(findings) - fails}, subjects=[subject])
    else:
        sys.stdout.write(render_text(name, findings))
    if fails:
        sys.exit(1)


def register(docs_sub, positive_int) -> None:
    """Add ``docs llms-txt`` under the ``docs`` group."""
    p = docs_sub.add_parser("llms-txt", help="Check an llms.txt file: structure, positioning sections, size")
    p.add_argument("path", metavar="PATH", help="an llms.txt file, or a directory with llms.txt at its top")
    p.add_argument("--token-budget", type=positive_int, default=llms_txt.TOKEN_BUDGET, metavar="N",
                   help=f"Warn when the file is over ~N tokens (default {llms_txt.TOKEN_BUDGET})")
    cli_flags.add(p, "output", "--json", action="store_true",
                  help="Print cli_output.v1 on stdout (the findings under data)")
    p.set_defaults(func=cmd_docs_llms_txt, emits_cli_output=True, cli_kind="docs llms-txt")
