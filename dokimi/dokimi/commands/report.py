# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""report subcommand group: eval, generate, model-compare."""

from __future__ import annotations

import argparse
import sys
import json
from pathlib import Path

from dokimi.report.markdown_report import write_compare_report, write_run_report


def cmd_report_model_compare(args: argparse.Namespace) -> None:
    """Compare multiple model runs and write a Markdown report."""
    from dokimi.report.model_compare import (
        check_run_consistency,
        generate_combined_report,
        load_run,
    )

    run_ids = [rid.strip() for rid in args.run_ids.split(",")]
    if not run_ids or any(not rid for rid in run_ids) or len(set(run_ids)) != len(run_ids):
        print("Error: --run-ids must contain unique, non-empty labels", file=sys.stderr)
        raise SystemExit(1)

    if not args.regular_runs and not args.golden_runs:
        print("Error: must provide at least --regular-runs or --golden-runs", file=sys.stderr)
        raise SystemExit(1)

    for flag, paths in (("--regular-runs", args.regular_runs), ("--golden-runs", args.golden_runs)):
        if paths and len(paths) != len(run_ids):
            print(
                f"Error: number of {flag} paths ({len(paths)}) must match "
                f"number of run IDs ({len(run_ids)})",
                file=sys.stderr,
            )
            raise SystemExit(1)

    regular_runs = []
    if args.regular_runs:
        for run_id, path in zip(run_ids, args.regular_runs, strict=True):
            print(f"Loading {run_id} (regular): {path}")
            regular_runs.append((run_id, load_run(path)))

    golden_runs = []
    if args.golden_runs:
        for run_id, path in zip(run_ids, args.golden_runs, strict=True):
            print(f"Loading {run_id} (golden): {path}")
            golden_runs.append((run_id, load_run(path)))

    # Consistency checks — hard errors abort, warnings go to stderr *and* into
    # the report body so a shared .md still carries them
    pinned_arm = getattr(args, "treatment_arm", None)
    pool_replicates = getattr(args, "pool_replicates", False)
    all_warnings: list[str] = []
    if regular_runs:
        all_warnings += check_run_consistency(
            regular_runs, "regular", pinned_arm, pool_replicates=pool_replicates
        )
    if golden_runs:
        all_warnings += check_run_consistency(
            golden_runs, "golden", pinned_arm, pool_replicates=pool_replicates
        )
    for w in all_warnings:
        print(w, file=sys.stderr)

    generate_combined_report(
        regular_runs,
        golden_runs,
        run_ids,
        args.out,
        treatment_arm=pinned_arm,
        correction=getattr(args, "stats_correction", "holm"),
        pool_replicates=pool_replicates,
        warnings=all_warnings,
    )


def cmd_report(args: argparse.Namespace) -> None:
    """Generate markdown report from JSON snapshot."""
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    out_md = Path(args.out_md).resolve()

    if "diff" in data:
        write_compare_report(data, out_md)
    else:
        write_run_report(data, out_md)


def cmd_report_eval(args: argparse.Namespace) -> None:
    """Generate quality report from eval JSON (dynamic vs static breakdown)."""
    from dokimi.records import RecordError
    from dokimi.report.eval_report import generate_report

    if args.out:
        out = args.out
    elif getattr(args, "run_id", None):
        out = f"results/{args.product.lower()}_{args.run_id}/reports/{args.product}_full.md"
    else:
        out = f"results/{args.product}/reports/{args.product}_full.md"
    try:
        generate_report(eval_path=args.eval, out_path=out)
    except RecordError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


def cmd_report_generate(args: argparse.Namespace) -> None:
    """Generate comprehensive analysis report from eval results."""
    from dokimi.records import RecordError
    from dokimi.report import ReportGenerator

    # Load eval and questions
    try:
        eval_data = json.loads(Path(args.eval).read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"Error: cannot read {args.eval}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:  # not UTF-8 or not JSON
        print(f"Error: {args.eval} is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if not isinstance(eval_data, dict):
        print(f"Error: {args.eval} must hold a JSON object", file=sys.stderr)
        raise SystemExit(1)
    from dokimi.questions.artifact import load_questions

    try:
        questions_data = load_questions(args.questions).artifact
    except ValueError as exc:  # ArtifactValidationError: names the record and the fix
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    evaluations = eval_data.get("evaluations", [])
    # A non-list is reported by the generator's RecordError check below.
    n_evals = len(evaluations) if isinstance(evaluations, list) else 0
    print(f"Loaded {n_evals} evaluations from {args.eval}")
    print(f"Loaded {len(questions_data.get('questions', []))} questions from {args.questions}")

    # Generate report
    generator = ReportGenerator()
    try:
        report = generator.generate_report(
            eval_data=eval_data,
            questions_data=questions_data,
            output_format=args.format
        )
    except RecordError as exc:
        print(f"Error: {args.eval} {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    # Save output
    output_path = Path(args.output) if args.output else Path(f"reports/{args.product}.{args.format}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")

    print(f"\n✅ Generated report: {output_path}")

    # Print summary to console
    if args.format == "markdown":
        print("\n" + "="*80)
        print(report.split("---")[0])  # Print just the summary section


def register(sub, positive_int) -> None:
    """Add the `report` subcommand group."""
    report_p = sub.add_parser("report", help="Generate analysis reports")
    report_sub = report_p.add_subparsers(dest="report_cmd", required=True)

    # report eval — lightweight, no questions needed
    eval_r_p = report_sub.add_parser("eval", help="Generate quality report from eval JSON (dynamic vs static breakdown)")
    eval_r_p.add_argument("--product", required=True, help="Product name (e.g., oneDAL)")
    eval_r_p.add_argument("--eval", required=True, help="Path to eval JSON file")
    eval_r_p.add_argument("--run-id", default=None, dest="run_id",
                         help="Run tag (e.g. gpt4o). Auto-sets output path.")
    eval_r_p.add_argument("--out", default=None, help="Output .md file (default: results/{product}/reports/{product}_full.md)")
    eval_r_p.set_defaults(func=cmd_report_eval)

    # report generate
    gen_r_p = report_sub.add_parser("generate", help="Generate comprehensive report from eval results")
    gen_r_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    gen_r_p.add_argument("--eval", required=True, help="Path to eval JSON file")
    gen_r_p.add_argument("--questions", required=True, help="Path to questions JSON file")
    gen_r_p.add_argument("--output", default=None, help="Output file (default: reports/{product}.md)")
    gen_r_p.add_argument("--format", default="markdown", choices=["markdown", "json"], help="Output format")
    gen_r_p.set_defaults(func=cmd_report_generate)

    # report model-compare
    mc_p = report_sub.add_parser(
        "model-compare",
        help="Compare multiple model runs over a common question set and write a Markdown report",
    )
    mc_p.add_argument(
        "--regular-runs",
        nargs="+",
        dest="regular_runs",
        help="Paths to regular-question arms JSON files (one per model, same order as --run-ids)",
    )
    mc_p.add_argument(
        "--golden-runs",
        nargs="+",
        dest="golden_runs",
        help="Paths to golden-question arms JSON files (one per model, same order as --run-ids)",
    )
    mc_p.add_argument(
        "--run-ids",
        required=True,
        dest="run_ids",
        help="Comma-separated run labels (e.g. sonnet46,opus48)",
    )
    mc_p.add_argument("--out", required=True, help="Output Markdown file path")
    mc_p.add_argument(
        "--treatment-arm",
        default=None,
        dest="treatment_arm",
        help=(
            "Name of the treatment arm to use as the scored context arm. "
            "Required when a run contains more than one non-baseline arm."
        ),
    )
    mc_p.add_argument(
        "--stats-correction",
        default="holm",
        dest="stats_correction",
        # Literal so registering the CLI stays stdlib-only; eval.stats.CORRECTIONS
        # is the source of truth and a test guards against drift.
        choices=["holm", "bh"],
        help=(
            "Multiple-comparison correction applied across the compared runs: "
            "holm (family-wise error, default) or bh (false discovery rate)."
        ),
    )
    mc_p.add_argument(
        "--pool-replicates",
        action="store_true",
        dest="pool_replicates",
        help=(
            "Average runs that share a configuration (same answer model/provider, "
            "judge, sampling, harness, plugin set and arms) into one row per "
            "configuration, instead of treating them "
            "as rival models. Required when any configuration appears more than once."
        ),
    )
    mc_p.set_defaults(func=cmd_report_model_compare)
