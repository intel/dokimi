# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`catalog routing-compare BASE OTHER`: two saved all-vs-all runs, query by query (#710)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dokimi.utils import InputError

#: How many changed picks the console lists; the whole list is in ``--out``.
SHOWN_CHANGES = 20


def add_routing_compare(catalog_sub) -> None:
    """`catalog routing-compare BASE OTHER [--arm lexical|model] [--out FILE]`: free, no model call."""
    from dokimi.novelty.routing_compare import ARMS

    parser = catalog_sub.add_parser(
        "routing-compare",
        help="Compare two saved `catalog routing` runs query by query (sign test, no model call)")
    parser.add_argument("base", metavar="BASE", help="A result `catalog routing --out` saved")
    parser.add_argument("other", metavar="OTHER", help="The result to compare with BASE")
    parser.add_argument("--arm", choices=ARMS, default="lexical",
                        help="Which arm's per-query rows to compare (default lexical); model "
                             "needs both runs made with --model")
    parser.add_argument("--out", default=None, help="Write the comparison as JSON to this file")
    parser.set_defaults(func=cmd_routing_compare)


def cmd_routing_compare(args: argparse.Namespace) -> None:
    """Compare the two runs, print the summary and, with ``--out``, save it as JSON."""
    from dokimi.novelty.routing import RoutingInputError
    from dokimi.novelty.routing_compare import compare_runs, load_run

    try:
        base, other = load_run(args.base, args.arm), load_run(args.other, args.arm)
        result = compare_runs(base, other, args.arm)
    except RoutingInputError as exc:
        raise InputError(str(exc)) from exc
    common = result["n_paired"] + sum(result["unscored"].values())
    if not common:
        raise InputError(f"{args.base} and {args.other} share no (gold, query) pair: "
                         f"{result['only_in_base']['count']} queries only in BASE, "
                         f"{result['only_in_other']['count']} only in OTHER")
    if args.out:
        out = Path(args.out)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"mode": "routing_compare", **result}, indent=2),
                           encoding="utf-8")
        except OSError as exc:
            raise InputError(f"cannot write --out {out}: {exc}") from exc
    print_comparison(result)


def _only_line(label: str, listed: dict) -> list[str]:
    """The queries one run lacks, by count and by example; nothing when there are none."""
    if not listed["count"]:
        return []
    shown = "; ".join(f"{e['gold']}: {e['query']!r}" for e in listed["examples"])
    return [f"{listed['count']} queries only in {label}, left out (e.g. {shown})"]


def print_comparison(result: dict) -> None:
    """The comparison as a readable summary on stdout."""
    arm = result["arm"]
    scoring = result["scoring"]
    right = result["n_correct"]
    n = result["n_paired"]
    discordant = result["discordant"]
    key = ("gold or acceptable" if scoring == "gold_or_acceptable" else "gold")
    print(f"routing-compare ({arm} arm): BASE {result['base']['path']}, OTHER {result['other']['path']}")
    print(f"{n} paired queries scored on {key}")
    print(f"BASE  {right['base']} of {n} right")
    print(f"OTHER {right['other']} of {n} right")
    if scoring == "gold_or_acceptable":
        gold = result["n_correct_gold"]
        print(f"      gold only: BASE {gold['base']}, OTHER {gold['other']}")
    print(f"right in BASE only: {discordant['only_base']}; right in OTHER only: "
          f"{discordant['only_other']}; exact two-sided sign test p = {result['p_value']:.4g} "
          f"over {discordant['n_discordant']} discordant")
    if result["acceptable_mismatch"]:
        print(f"note: {result['acceptable_mismatch']} paired queries have acceptable alternatives "
              "in one run only, or different ones; they are scored on the gold alone")
    for reason, count in result["unscored"].items():
        print(f"{count} paired queries left out, unscored in a run ({reason.replace('_', ' ')})")
    for label, listed in (("BASE", result["only_in_base"]), ("OTHER", result["only_in_other"])):
        for line in _only_line(label, listed):
            print(line)
    changed = result["changed"]
    if changed:
        print(f"{len(changed)} picks changed:")
        for row in changed[:SHOWN_CHANGES]:
            print(f"  {row['gold']}: {row['query']!r}: {row['base']} -> {row['other']}")
        if len(changed) > SHOWN_CHANGES:
            print(f"  ... and {len(changed) - SHOWN_CHANGES} more (--out has them all)")
