# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval coverage``: coverage and relevance of an eval set against a frozen need graph (#331)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE


METRICS = ("weighted", "core_rare_critical", "rare_critical")


def _floor(value: str) -> tuple[str, float]:
    metric, sep, number = value.rpartition("=")
    metric = metric if sep else "weighted"
    if metric not in METRICS:
        raise argparse.ArgumentTypeError(f"metric must be one of {', '.join(METRICS)}, got {metric!r}")
    try:
        floor = float(number)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {number!r}") from None
    if not 0.0 <= floor <= 1.0:
        raise argparse.ArgumentTypeError(f"must lie in 0-1, got {floor}")
    return metric, floor


def _below(report: dict, floors: list[tuple[str, float]]) -> list[str]:
    from dokimi.eval_coverage import exact_shares

    exact = exact_shares(report)
    out = []
    for metric, floor in floors:
        share = exact[metric]
        if share is not None and share < floor:
            out.append(f"{metric} coverage {share:.6f} < {floor:.6f}")
    return out


def cmd_eval_coverage(args: argparse.Namespace) -> None:
    """Print the report; exit 2 on an unreadable input or a bad ``needs`` label, 1 under a floor."""
    from dokimi.artifacts import ArtifactValidationError, save_artifact
    from dokimi.eval_coverage import coverage_report, input_files, load_graph, read_labels, render_markdown

    root = Path(args.evals)
    if not root.exists():
        print(f"Error: {root} does not exist", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    try:
        graph = load_graph(Path(args.graph))
        evals = read_labels(root)
    except (OSError, ValueError, ArtifactValidationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if not evals:
        print(f"Error: no evals.json rows or tasks under {root}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if args.out and Path(args.out).resolve() in {Path(args.graph).resolve(), *input_files(root)}:
        print(f"Error: --out would overwrite an input file: {args.out}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    report = coverage_report(graph, evals, str(root))
    if args.out:
        try:
            report = save_artifact("eval_coverage", report, Path(args.out))
        except OSError as exc:
            print(f"Error: cannot write {args.out}: {exc.strerror or exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
    print(render_markdown(report))
    if report["problems"]:
        print(f"Error: {len(report['problems'])} problem(s) with the evals' needs labels",
              file=sys.stderr)
        sys.exit(EXIT_USAGE)
    failed = _below(report, args.min or [])
    if failed:
        print("FAIL: " + "; ".join(failed), file=sys.stderr)
        sys.exit(EXIT_FAILURE)


def register(eval_sub) -> None:
    """Add ``eval coverage`` to the ``eval`` group."""
    p = eval_sub.add_parser(
        "coverage",
        help="Coverage of a frozen need graph by an eval set's `needs` labels (no model call)",
    )
    p.add_argument("evals", help="evals.json, a skill directory (evals/evals.json) or a tasks root")
    p.add_argument("--graph", required=True,
                   help="need_graph.v1 file (a novelty need graph is valid as written)")
    p.add_argument("--out", default=None, help="Write the eval_coverage.v1 artifact here")
    p.add_argument("--min", type=_floor, action="append", metavar="[METRIC=]X",
                   help="Exit 1 when a coverage figure is below X (0-1); METRIC is weighted "
                        "(default), core_rare_critical or rare_critical. Repeatable")
    p.set_defaults(func=cmd_eval_coverage)
