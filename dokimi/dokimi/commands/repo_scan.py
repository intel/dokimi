# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo scan` helpers kept out of commands/repo.py, which is at its size cap: the `--typecheck` check and the text report."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from dokimi.exit_codes import EXIT_USAGE


def _typecheck_or_exit(args: argparse.Namespace):
    """The ``--typecheck`` check, or None; ``--python-env`` needs it and must name an interpreter."""
    from dokimi.repos.l0 import Check

    if not args.typecheck:
        if args.python_env is not None:
            print("✗ --python-env needs --typecheck", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        if args.typecheck_tool is not None:
            print("✗ --typecheck-tool needs --typecheck", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        return None
    tool = args.typecheck_tool or "mypy"
    # a directory is X_OK too: only a regular file can be the interpreter
    if args.python_env is not None and not (os.path.isfile(args.python_env) and os.access(args.python_env, os.X_OK)):
        print(f"✗ --python-env {args.python_env}: not an executable interpreter", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if tool != "mypy" and shutil.which(tool) is None:
        print(f"✗ --typecheck-tool {tool}: not on PATH", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    python = os.path.abspath(args.python_env) if args.python_env is not None else None  # the tool runs elsewhere
    return Check(tool, python)


def _print_scan_text(manifest, payload: dict, out: str | None) -> None:
    """The text report of ``repo scan``: the counts, the warnings, the typecheck and static lines, the FAIL lines."""
    c = payload["counts"]
    detected = " (detected)" if payload["adapters_detected"] else ""
    print(f"{manifest.id}: {len(payload['files'])} guidance files in {payload['repo']['tree']}; "
          f"adapters: {', '.join(payload['adapters']) or 'none'}{detected}")
    print(f"  claims: {c['total']} (ok {c['ok']}, unresolved {c['unresolved']}, fail {c['fail']})")
    for warning in payload["warnings"]:
        print(f"  warning: {warning}")
    if payload["incomplete"]:
        print(f"  incomplete: {'; '.join(payload['incomplete_reasons'])} (repo scan --fail-on-fail exits 1)")
    tc = payload["typecheck"]
    if tc is not None:
        where = tc["python"] or "no --python-env: unknown"
        print(f"  typecheck {tc['tool']} {tc['tool_version'] or ''} ({where}): {tc['fences']} python fences, "
              f"{tc['checked']} checked, {tc['fail']} fail, {tc['unknown']} unknown, {tc['syntax']} not Python")
    static_info = payload["static"]
    if static_info is not None and not static_info["errors"]:
        r = payload["by_source"]["repository"]
        print(f"  repository static: {r['total']} claims, {r['fail']} fail; "
              f"{static_info['superseded']} framework claims replaced")
    for claim in payload["claims"]:
        if claim["status"].split("_", 1)[0] == "fail":
            print(f"  FAIL {claim['file']}:{claim['line']} [{claim.get('kind')}] "
                  f"{str(claim.get('claim'))[:90]} -- {claim.get('why')}")
    if out:
        print(f"wrote {out}")
