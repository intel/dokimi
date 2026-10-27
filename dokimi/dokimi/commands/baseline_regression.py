# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Opt-in, matched-question promotion checks for saved evaluation baselines."""

from __future__ import annotations

import argparse
import math
import sys
from decimal import Decimal

from dokimi.records import BASELINE, CONTEXT, Record


def threshold(value: str) -> float:
    """argparse type for a permitted drop in score points."""
    try:
        number = float(value)
        if math.isfinite(number) and number >= 0:
            return number
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected a finite non-negative number")


def enabled(args: argparse.Namespace) -> bool:
    return any(getattr(args, name, None) is not None for name in ("max_context_drop", "max_lift_drop"))


def _scored(rows: list[Record]) -> dict[str, tuple[Decimal, Decimal]]:
    values = {}
    for row in rows:
        context, baseline = row.score(CONTEXT), row.score(BASELINE)
        if row.error or any(s.error for s in row.scores.values()):
            continue
        if context is not None and baseline is not None and math.isfinite(context) and math.isfinite(baseline):
            context_value = Decimal(str(context))
            values[row.key] = (context_value, context_value - Decimal(str(baseline)))
    return values


def check(args: argparse.Namespace, baseline: list[Record], current: list[Record]) -> None:
    """Require complete unique paired evidence, then compare unrounded score drops."""
    if not enabled(args):
        return
    base, cur = _scored(baseline), _scored(current)
    matched = base.keys() & cur.keys()
    minimum = getattr(args, "min_matched", 1)
    if len(matched) < minimum or len(matched) != len(baseline) or len(matched) != len(current):
        print(f"Regression gate: insufficient or partial evidence ({len(matched)} matched scored, "
              f"{len(baseline)} baseline, {len(current)} current; minimum {minimum}; "
              "complete unique coverage required)", file=sys.stderr)
        raise SystemExit(1)
    failed = False
    for index, name in enumerate(("max_context_drop", "max_lift_drop")):
        limit = getattr(args, name, None)
        if limit is None:
            continue
        drop = sum((base[key][index] - cur[key][index] for key in sorted(matched)), Decimal(0)) / len(matched)
        failed |= drop > Decimal(str(limit))
        metric = "context score" if index == 0 else "paired lift"
        print(f"{metric}: drop={drop:.12g}, allowed={limit:.12g} ({len(matched)} matched)")
    print("Regression gate: " + ("REGRESSION" if failed else "PASS"))
    if failed:
        raise SystemExit(1)
