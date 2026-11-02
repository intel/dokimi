# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Drive the reach conformance suite (docs/reach-matrix.md).

    python scripts/reach_conformance.py check
    python scripts/reach_conformance.py run --harness codex=<model> --out DIR
    python scripts/reach_conformance.py merge DIR/*.matrix.json --out DIR/reach-matrix.json --md DIR/reach-matrix.md

``run`` makes paid model calls for every harness that has a model, a credential and a CLI here; the rest are
written as skipped runs. Run it from the dokimi project directory with ``PYTHONPATH=.``: a script's own directory,
not the project, is on the import path, and ``make dev`` does not install the package.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dokimi.artifacts import load_artifact
from dokimi.repos import conformance as cf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "data" / "reach-conformance"
DEFAULT_EXPECTATIONS = DEFAULT_FIXTURES / "expectations.yaml"


def _expectations(path: Path) -> dict:
    return cf.load_expectations(path)


def cmd_check(args: argparse.Namespace) -> int:
    problems = cf.check_expectations(_expectations(args.expectations), args.fixtures)
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"ok: {len(cf.cases(args.fixtures))} fixtures, expectations agree")
    return 1 if problems else 0


def _harness_spec(text: str) -> tuple[cf.Harness, str]:
    name, _, model = text.partition("=")
    if name not in cf.HARNESSES:
        raise argparse.ArgumentTypeError(f"unknown harness {name!r}; known: {', '.join(cf.HARNESSES)}")
    return cf.HARNESSES[name], model


def cmd_run(args: argparse.Namespace) -> int:
    expect = _expectations(args.expectations)
    problems = cf.check_expectations(expect, args.fixtures)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    names = [harness.name for harness, _ in args.harness]
    if len(set(names)) != len(names):
        print("✗ each --harness may be given once: a second run would overwrite the first's matrix", file=sys.stderr)
        return 1
    failed = False
    args.out.mkdir(parents=True, exist_ok=True)
    for harness, model in args.harness:
        matrix = cf.run_harness(harness, model, args.fixtures, expect, args.out / f"{harness.name}-runs",
                                timeout=args.timeout, budget=args.budget, env=os.environ)
        cf.write_matrix(matrix, args.out / f"{harness.name}.matrix.json")
        counts = matrix["summary"]["runs"]
        print(f"{harness.name}: ran {counts['ran']}, errors {counts['errors']}, "
              f"failed {counts['failed']}, skipped {counts['skipped']}")
        failed = failed or counts["failed"] > 0
    return 1 if failed else 0


def cmd_merge(args: argparse.Namespace) -> int:
    expect = _expectations(args.expectations)
    matrices = [load_artifact("reach_matrix", path) for path in args.inputs]
    merged = cf.merge(matrices)
    if merged["expectations_digest"] != cf.expectations_digest(expect):
        print(f"✗ the matrices were built from another revision of {args.expectations}", file=sys.stderr)
        return 1
    cf.write_matrix(merged, args.out)
    summaries = {case: block["summary"] for case, block in expect["cases"].items()}
    args.md.parent.mkdir(parents=True, exist_ok=True)
    args.md.write_text(cf.render_markdown(merged, summaries), encoding="utf-8")
    print(f"merged {len(matrices)} matrices: {len(merged['runs'])} runs, {len(merged['cells'])} cells")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--expectations", type=Path, default=DEFAULT_EXPECTATIONS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="verify the expectations against the fixture tree")
    run = sub.add_parser("run", help="run reach over the fixtures for each harness")
    run.add_argument("--harness", action="append", type=_harness_spec, required=True,
                     metavar="NAME[=MODEL]", help="repeatable; no model makes the harness skipped")
    run.add_argument("--out", type=Path, required=True, help="directory for <harness>.matrix.json")
    run.add_argument("--timeout", type=int, default=cf.DEFAULT_TIMEOUT_S, help="per-scenario seconds")
    run.add_argument("--budget", type=float, default=cf.DEFAULT_BUDGET_USD, help="per-scenario USD cap")
    merge = sub.add_parser("merge", help="merge matrices into one JSON and one markdown page")
    merge.add_argument("inputs", nargs="+", type=Path)
    merge.add_argument("--out", type=Path, required=True)
    merge.add_argument("--md", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {"run": cmd_run, "merge": cmd_merge, "check": cmd_check}
    try:
        return commands[args.command](args)
    except (cf.ConformanceError, OSError, ValueError) as exc:  # an input the suite cannot use: say so, no traceback
        print(f"✗ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
