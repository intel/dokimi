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


def cmd_novelty_ground(args: argparse.Namespace) -> None:
    """Write ``identifiers`` vetoes from each ``--tool``'s own ``--help`` into an atoms file."""
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.novelty.grounding import add_checks, help_text, identifiers_check, verify_keys

    _, _, data = load_atoms_record(args.atoms)
    checks = [identifiers_check(tool, help_text(tool), version=args.tool_version)
              for tool in args.tool]
    changed = add_checks(data.get("atoms", []), checks, args.tool)
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
        print(f"{check['source']['tool']}: {len(check['known'])} options "
              f"(help sha256 {check['source']['sha256'][:12]})", file=sys.stderr)
    print(f"{changed} of {len(data.get('atoms', []))} atoms got a veto check -> {out}",
          file=sys.stderr)
    print("answer keys: " + ", ".join(f"{n} {status}" for status, n in keys.items())
          + " (not_found is a review flag: negative and version-bound claims land there)",
          file=sys.stderr)
