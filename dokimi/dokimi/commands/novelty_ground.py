# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""``skill novelty ground``: identifier vetoes from a tool's own ``--help``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_novelty_ground(args: argparse.Namespace) -> None:
    """Write ``identifiers`` vetoes from each ``--tool``'s own ``--help`` into an atoms file."""
    from dokimi.novelty.atoms import load_atoms_record
    from dokimi.novelty.grounding import add_checks, help_text, identifiers_check

    _, _, data = load_atoms_record(args.atoms)
    checks = [identifiers_check(tool, help_text(tool), version=args.tool_version)
              for tool in args.tool]
    changed = add_checks(data.get("atoms", []), checks, args.tool)
    out = Path(args.out or args.atoms)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for check in checks:
        print(f"{check['source']['tool']}: {len(check['known'])} options "
              f"(help sha256 {check['source']['sha256'][:12]})", file=sys.stderr)
    print(f"{changed} of {len(data.get('atoms', []))} atoms got a veto check -> {out}",
          file=sys.stderr)
