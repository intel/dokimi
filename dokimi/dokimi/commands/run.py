# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""run and compare subcommands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dokimi.report.json_report import write_json
from dokimi.report.markdown_report import write_compare_report, write_run_report
from dokimi.runner.compare import compare_snapshots
from dokimi.runner.run import DocLoadError, _load_spec, run_benchmark, save_snapshot


def _run_or_exit(root: Path, spec_path: Path) -> dict:
    """Run the benchmark; a bad spec exits 2 (1 is the verdict code of --strict), a bad doc exits 1."""
    try:
        _load_spec(spec_path)  # fail on a spec that cannot be read, parsed or validated, before the run
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {spec_path}: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        return run_benchmark(root, spec_path)
    except DocLoadError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_run(args: argparse.Namespace) -> None:
    """Run benchmark and save snapshot + report."""
    root = Path(args.root).resolve()
    spec_path = Path(args.spec).resolve()
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()

    result = _run_or_exit(root, spec_path)
    save_snapshot(result, out_json)
    write_run_report(result, out_md)
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
        bands = check_critical_bands(result["summary"], spec)

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


def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two snapshots and show regression analysis."""
    base = Path(args.base).resolve()
    cand = Path(args.candidate).resolve()
    spec_path = Path(args.spec).resolve() if args.spec else None
    out_json = Path(args.out_json).resolve()
    out_md = Path(args.out_md).resolve()

    spec_data = None
    try:
        if spec_path:
            import yaml
            try:
                spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, yaml.YAMLError) as exc:
                raise ValueError(f"Invalid spec {spec_path}: {exc}") from exc
            if spec_data is not None and not isinstance(spec_data, dict):
                raise ValueError(f"Invalid spec {spec_path}: root must be a mapping")
        result = compare_snapshots(base, cand, spec=spec_data)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    write_json(result, out_json)
    write_compare_report(result, out_md)
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
