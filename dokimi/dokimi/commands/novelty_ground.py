# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""``skill novelty ground``: identifier vetoes from a tool's own ``--help``."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from dokimi.utils import InputError


def _refuse_bad_sources(tools: list[str], headers: list[str], libraries: list[str],
                        prefixes: list[str], tool_version: str | None = None) -> None:
    """Refuse a source set the flags cannot read: ``--prefix`` goes with a header or a shared library."""
    if tool_version and not tools:
        raise InputError("--tool-version names the version of --tool: give --tool with it")
    if prefixes and not (headers or libraries):
        raise InputError("--prefix scopes the names of --header and --shared-lib: give one of them")
    if (headers or libraries) and not prefixes:
        raise InputError("--header and --shared-lib need --prefix: it says which names are checked")
    if not (tools or headers or libraries):
        raise InputError("give --tool, --header or --shared-lib: nothing to ground the atoms on")


def _describe(check: dict) -> str:
    """The stderr line for one check: its source, how many names it lists, and the pinned sha256."""
    source = check["source"]
    if source.get("kind") == "symbols":
        inputs = source["inputs"]
        return (f"symbols {' '.join(source['prefixes'])}: {len(check['known'])} names from "
                f"{len(inputs)} files (sha256 {'; '.join(i['sha256'][:12] for i in inputs)})")
    return (f"{source['tool']}: {len(check['known'])} options "
            f"(help sha256 {source['sha256'][:12]})")


def cmd_novelty_ground(args: argparse.Namespace) -> None:
    """Write ``identifiers`` vetoes from each ``--tool``'s ``--help`` and each source's names."""
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.novelty.grounding import add_checks, help_text, identifiers_check, verify_keys
    from dokimi.novelty.symbols import symbols_check

    tools, headers = list(args.tool or []), list(args.header or [])
    libraries, prefixes = list(args.shared_lib or []), list(args.prefix or [])
    _refuse_bad_sources(tools, headers, libraries, prefixes, args.tool_version)
    _, _, data = load_atoms_record(args.atoms)
    checks = [identifiers_check(tool, help_text(tool), version=args.tool_version) for tool in tools]
    if headers or libraries:
        checks.append(symbols_check(prefixes, headers, libraries))
    changed = add_checks(data.get("atoms", []), checks)
    keys = verify_keys(data.get("atoms", []))
    out = Path(args.out or args.atoms).resolve()  # a symlink: its target is what is rewritten
    try:  # as save_atoms does, an output in a new directory creates it
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise InputError(f"cannot create the output directory {out.parent}: {exc.strerror or exc}") from None
    # Write beside the target and replace it only after the write succeeds: the default --out is
    # the atoms file itself, and a failed write must not leave it truncated.
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")  # own name: two runs do not share it
    try:
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            if out.exists():
                shutil.copymode(out, tmp)  # the rewritten file keeps the mode it had
            os.replace(tmp, out)
        except OSError as exc:  # a usage error with one line, as the mkdir above
            raise InputError(f"cannot write {out}: {exc.strerror or exc}") from None
    finally:
        tmp.unlink(missing_ok=True)
    for check in checks:
        print(_describe(check), file=sys.stderr)
    print(f"{changed} of {len(data.get('atoms', []))} atoms got a veto check -> {out}",
          file=sys.stderr)
    print("answer keys: " + ", ".join(f"{n} {status}" for status, n in keys.items())
          + " (not_found is a review flag: a claim names an option the help lacks;"
          " a negative claim is unverifiable)",
          file=sys.stderr)
