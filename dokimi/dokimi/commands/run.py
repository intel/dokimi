# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""run and compare subcommands."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from dokimi.exit_codes import EXIT_USAGE
from dokimi.metrics.example_runner import ExecutionRefused
from dokimi.report.json_report import write_json
from dokimi.report.markdown_report import write_compare_report, write_run_report
from dokimi.runner.compare import CompareInputError, compare_snapshots
from dokimi.runner.run import DocLoadError, load_benchmark_spec, run_benchmark, save_snapshot
from dokimi.runner.spec import DocSelectionError


def _run_or_exit(root: Path, spec_path: Path) -> dict:
    """Run the benchmark; a bad spec, an unreadable doc, a doc selection it refuses or
    example limits the host cannot apply exit 2.

    Exit 1 is the verdict code of --strict (see cmd_run).
    """
    try:
        load_benchmark_spec(spec_path)  # fail on a spec that cannot be read, parsed or validated, before the run
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {spec_path}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    try:
        return run_benchmark(root, spec_path)
    except DocLoadError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    except (DocSelectionError, ExecutionRefused) as exc:  # e.g. fewer docs than min_docs, a cap the host lacks
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _write_outputs(writes) -> None:
    """Run each (writer, data, path); an unwritable path exits 2 naming it."""
    for writer, data, path in writes:
        try:
            writer(data, path)
        except OSError as exc:
            print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
            sys.exit(EXIT_USAGE)


def cmd_run(args: argparse.Namespace) -> None:
    """Run benchmark and save snapshot + report."""
    root = Path(args.root).resolve()
    spec_path = Path(args.spec).resolve()
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()

    result = _run_or_exit(root, spec_path)
    _write_outputs(((save_snapshot, result, out_json), (write_run_report, result, out_md)))
    print(json.dumps(result["summary"], indent=2))

    # Strict mode: check hard gate and critical bands
    if args.strict:
        # Re-load spec for gate checks (run_benchmark loads internally)
        import yaml
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
        if spec is None:
            spec = {}
        elif not isinstance(spec, dict):
            raise ValueError("Spec must be a YAML mapping/dict")

        from dokimi.gate.score_gate import check_gate
        from dokimi.gate.critical_bands import check_critical_bands

        hard_gate = check_gate(result["summary"], spec, "hard")
        try:
            bands = check_critical_bands(result["summary"], spec)
        except ValueError as exc:  # a band the gate cannot read: a bad spec, named, not a traceback
            print(f"Error: {spec_path}: {exc}", file=sys.stderr)
            sys.exit(2)

        # Exit 1 if hard gate fails
        if hard_gate.enabled and not hard_gate.passed:
            print(f"\n❌ HARD GATE FAILED: score {hard_gate.actual_score:.4f} < {hard_gate.min_score:.4f}", file=sys.stderr)
            sys.exit(1)

        # Exit 1 if any critical band violated
        if bands.has_violations:
            print("\n❌ CRITICAL BAND VIOLATIONS:", file=sys.stderr)
            for v in bands.violations:
                if v.violated:
                    print(f"  - {v.condition}: {v.actual:.4f} < {v.threshold:.4f}", file=sys.stderr)
            sys.exit(1)

        # A score over no document passes any gate set at 0 or switched off.
        if not result["summary"].get("docs"):
            print(f"\n❌ incomplete: no document was measured under {root} (0 docs)", file=sys.stderr)
            sys.exit(1)


def _load_compare_spec(spec_path: Path) -> dict | None:
    """The `compare --spec` mapping; any unreadable or wrong-shape spec is a ``CompareInputError`` naming the file."""
    import yaml

    try:
        spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError, yaml.YAMLError) as exc:
        raise CompareInputError(f"Invalid spec {spec_path}: {exc}") from exc
    if spec_data is not None and not isinstance(spec_data, dict):
        raise CompareInputError(f"Invalid spec {spec_path}: root must be a mapping")
    thresholds = (spec_data or {}).get("thresholds", {})
    if not isinstance(thresholds, dict):
        raise CompareInputError(f"Invalid spec {spec_path}: `thresholds` must be a mapping")
    regressions = thresholds.get("regressions", {})
    if not isinstance(regressions, dict):
        raise CompareInputError(f"Invalid spec {spec_path}: `thresholds.regressions` must be a mapping")
    for name, value in regressions.items():
        if not _non_negative_number(value):
            raise CompareInputError(
                f"Invalid spec {spec_path}: `thresholds.regressions.{name}` must be a finite non-negative number, got {value!r}"
            )
    return spec_data


def _non_negative_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two snapshots and show regression analysis."""
    base = Path(args.base).resolve()
    cand = Path(args.candidate).resolve()
    spec_path = Path(args.spec).resolve() if args.spec else None
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()

    try:
        spec_data = _load_compare_spec(spec_path) if spec_path else None
        result = compare_snapshots(base, cand, spec=spec_data)
    except CompareInputError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    _write_outputs(((write_json, result, out_json), (write_compare_report, result, out_md)))
    print(json.dumps(result["diff"], indent=2))


def register(sub, positive_int) -> None:
    """Add the `run` and `compare` subparsers."""
    run_p = sub.add_parser("run")
    run_p.add_argument("--root", default=".")
    run_p.add_argument("--spec", default="benchmarks/spec.v1.yaml")
    run_p.add_argument("--out-json", default="baselines/current.json")
    run_p.add_argument("--out-md", default="reports/current.md")
    run_p.add_argument("--strict", action="store_true", help="Enable hard gate and critical bands (exit 1 on failure)")
    run_p.set_defaults(func=cmd_run)


def register_compare(sub, positive_int) -> None:
    """Add the `compare` subparser."""
    cmp_p = sub.add_parser("compare")
    cmp_p.add_argument("--base", required=True)
    cmp_p.add_argument("--candidate", required=True)
    cmp_p.add_argument("--spec", default=None, help="Spec file for regression thresholds")
    cmp_p.add_argument("--out-json", default="reports/compare.json")
    cmp_p.add_argument("--out-md", default="reports/compare.md")
    cmp_p.set_defaults(func=cmd_compare)
