# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval coverage``: coverage and relevance of an eval set against a frozen need graph (#331)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE


def _floor(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number, got {value!r}") from None
    if not 0.0 <= number <= 1.0:
        raise argparse.ArgumentTypeError(f"must lie in 0-1, got {number}")
    return number


def _below(report: dict, args: argparse.Namespace) -> list[str]:
    cov = report["coverage"]
    failed = []
    for name, floor, value in (("weighted", args.min_coverage, cov["weighted"]),
                               ("rare_critical", args.min_rare_critical, cov["rare_critical"])):
        if floor is not None and value is not None and value < floor:
            failed.append(f"{name} coverage {value:.2f} < {floor:.2f}")
    return failed


def cmd_eval_coverage(args: argparse.Namespace) -> None:
    """Print the report; exit 2 on an unreadable input or unknown need ids, 1 under a floor."""
    from dokimi.artifacts import ArtifactValidationError, save_artifact
    from dokimi.eval_coverage import coverage_report, load_graph, read_labels, render_markdown

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
    report = coverage_report(graph, evals, str(root))
    if args.out:
        report = save_artifact("eval_coverage", report, Path(args.out))
    print(json.dumps(report, indent=2) if args.format == "json" else render_markdown(report))
    if report["problems"]:
        print(f"Error: {len(report['problems'])} eval(s) name need ids the graph does not have",
              file=sys.stderr)
        sys.exit(EXIT_USAGE)
    failed = _below(report, args)
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
    p.add_argument("--format", choices=["markdown", "json"], default="markdown",
                   help="What stdout shows (default: markdown)")
    p.add_argument("--min-coverage", type=_floor, default=None, dest="min_coverage",
                   help="Exit 1 when weighted coverage is below this (0-1)")
    p.add_argument("--min-rare-critical", type=_floor, default=None, dest="min_rare_critical",
                   help="Exit 1 when rare_critical coverage is below this (0-1)")
    p.set_defaults(func=cmd_eval_coverage)
