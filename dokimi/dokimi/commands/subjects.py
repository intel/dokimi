# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""subjects subcommand group: subject descriptors and scorecards."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dokimi.llm import ANSWER_PROVIDERS
from dokimi.commands._output import exit_on_unwritable
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_MODEL_FOR_PROVIDER,
    JUDGE_MODEL_HELP,
)
from dokimi.exit_codes import EXIT_USAGE, usage_exit


def cmd_subjects_list(args: argparse.Namespace) -> None:
    """List subject descriptors below a root directory."""
    # Subject descriptors live in an evaluation-content checkout, so there is no
    # in-repo default root. Name the flag instead of printing an empty list that
    # reads like "no subjects exist".
    if not args.subjects_root:
        usage_exit("no subjects root given; pass --subjects-root <checkout>/evaluation/subjects")
    root = Path(args.subjects_root)
    if not root.is_dir():
        usage_exit(f"--subjects-root is not a directory: {root}")
    paths = sorted([*root.glob("*.toml"), *root.glob("*.json")])
    for path in paths:
        try:
            from dokimi.subjects import load_subject

            descriptor = load_subject(path)
            print(f"{descriptor.id}\t{descriptor.subject.kind}\t{path}")
        except Exception as exc:
            print(f"{path}\tERROR\t{exc}")


def cmd_subjects_show(args: argparse.Namespace) -> None:
    """Print the normalized subject plan."""
    from dokimi.subjects.loader import awareness_arm_specs, load_subject, matrix_config, work_arm_specs

    try:
        descriptor = load_subject(args.subject)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error loading subject: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    if descriptor.subject.kind == "repo":
        print(json.dumps({
            "id": descriptor.id,
            "subject": {
                "kind": "repo",
                "ref": descriptor.subject.ref,
                "manifest": descriptor.subject.manifest,
            },
            "baseline": descriptor.baseline,
        }, indent=2))
        return

    print(json.dumps({
        "id": descriptor.id,
        "subject": {
            "kind": descriptor.subject.kind,
            "ref": descriptor.subject.ref,
            "members": [member.__dict__ for member in descriptor.subject.members],
        },
        "suite": {
            "products": list(descriptor.suite.products),
            "questions": list(descriptor.suite.questions),
            "tasks": list(descriptor.suite.tasks),
        },
        "baseline": descriptor.baseline,
        "awareness_arm_specs": awareness_arm_specs(descriptor),
        "work_arm_specs": work_arm_specs(descriptor),
        "matrix": matrix_config(descriptor),
    }, indent=2))


def cmd_subjects_run(args: argparse.Namespace) -> None:
    """Run awareness matrix cells for one subject and write a scorecard."""
    from dokimi.artifacts import load_artifact, save_artifact
    from dokimi.commands.arms import _safe_name, run_config_from_args, run_matrix
    from dokimi.report.subject_report import render_subject_scorecard
    from dokimi.subjects import build_subject_scorecard, load_subject
    from dokimi.subjects.loader import awareness_arm_specs
    from dokimi.subjects.novelty_track import load_novelty_artifacts

    try:
        descriptor = load_subject(args.subject)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error loading subject: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    if descriptor.subject.kind == "repo":
        print("Error: repo subjects are not run by `subjects run` yet; "
              "check them with `repo validate`", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    # Attached, not produced: a novelty run costs atoms x probes x arms x (answer + grade), so it is
    # produced by `novelty analyze` and named here by path. A bad path fails now, before any cell
    # runs, rather than after the expensive part.
    try:
        novelty_artifacts, novelty_sources = load_novelty_artifacts(
            list(getattr(args, "novelty_artifact", None) or [])
        )
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"Error loading novelty artifact: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    try:
        work_tasks = _validate_subject_work_args(descriptor, args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error loading subject work: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    out_dir, baseline_arm, questions = _run_plan(descriptor, args)
    matrix_path = out_dir / "matrix.cells.json"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        matrix_path.write_text(json.dumps(_matrix_config_for_run(descriptor, args), indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"Error: cannot write {out_dir}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)

    awareness_runs: list[dict] = []
    for product, questions_path in zip(descriptor.suite.products, questions, strict=True):
        run_dir = out_dir / _safe_name(product)
        rollup_json = run_dir / "matrix-rollup.json"
        rollup_md = run_dir / "matrix-rollup.md"
        run_matrix(
            run_config_from_args(args, product=product, baseline_arm=baseline_arm),
            arms=",".join(awareness_arm_specs(descriptor)), questions=questions_path,
            matrix_cells=str(matrix_path), out_dir=run_dir, out_json=rollup_json, out_md=rollup_md,
            plugin_deltas=not args.no_plugin_deltas,
        )
        awareness_runs.append({
            "product": product,
            "questions": questions_path,
            "matrix_rollup": str(rollup_json),
            "rollup": load_artifact("matrix_rollup", rollup_json),
        })

    work_run = _run_subject_work(descriptor, args, out_dir=out_dir, tasks=work_tasks)
    scorecard = build_subject_scorecard(
        descriptor,
        awareness_runs=awareness_runs,
        work_run=work_run,
        out_dir=out_dir,
        novelty_artifacts=novelty_artifacts,
        novelty_sources=novelty_sources,
    )
    out_json = Path(args.out_json) if args.out_json else out_dir / "subject-scorecard.json"
    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    with exit_on_unwritable(out_json):
        save_artifact("subject_scorecard", scorecard, out_json)
    with exit_on_unwritable(out_md):
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(render_subject_scorecard(scorecard), encoding="utf-8")
    print(f"OK Saved subject scorecard: {out_json}")
    print(f"OK Saved subject report:    {out_md}")


def cmd_subjects_evidence(args: argparse.Namespace) -> None:
    """Render a saved subject scorecard as a skill_evidence.v1 document; no model calls."""
    from dokimi.artifacts import ArtifactValidationError, load_artifact, save_artifact
    from dokimi.subjects.evidence import build_skill_evidence

    try:
        scorecard = load_artifact("subject_scorecard", Path(args.scorecard))
    except (OSError, ValueError, ArtifactValidationError) as exc:
        usage_exit(f"cannot read subject scorecard {args.scorecard}: {exc}")
    try:
        evidence = build_skill_evidence(scorecard, source=str(args.scorecard))
    except (AttributeError, TypeError) as exc:
        usage_exit(f"{args.scorecard} is not a well-formed subject scorecard: {exc}")
    if not evidence["results"]:
        usage_exit(f"{args.scorecard} holds no awareness arm with a baseline to compare against: "
                   "nothing to publish")
    out = Path(args.out) if args.out else Path(args.scorecard).with_name("skill-evidence.json")
    try:
        out_real = out.resolve()
        # save_artifact keeps and removes "<out>.rejected"; that sibling must not be the scorecard.
        stale_real = out.with_name(out.name + ".rejected").resolve()
        card_real = Path(args.scorecard).resolve()
    # A symlink loop raises RuntimeError before Python 3.13; "." or "/" has no name, so with_name raises ValueError.
    except (OSError, RuntimeError, ValueError) as exc:
        usage_exit(f"cannot resolve --out {out}: {exc}")
    if out_real == card_real:
        usage_exit("--out names the scorecard itself; pick another file")
    if stale_real == card_real:
        usage_exit(f"{out}.rejected is the scorecard; a save to {out} would delete or overwrite it; pick another --out")
    try:
        with exit_on_unwritable(out):
            save_artifact("skill_evidence", evidence, out)
    except ArtifactValidationError as exc:
        usage_exit(f"{args.scorecard} holds a value the evidence schema rejects: {exc}")
    for warning in evidence["warnings"]:
        print(f"WARN {warning}", file=sys.stderr)
    print(f"OK Saved skill evidence: {out} ({len(evidence['results'])} results)")


def register(sub, positive_int) -> None:
    """Add the `subjects` subcommand group."""
    subjects_p = sub.add_parser(
        "subjects",
        help="Run evaluation subjects (artifact + suite) into scorecards",
    )
    subjects_sub = subjects_p.add_subparsers(dest="subjects_cmd", required=True)

    list_p = subjects_sub.add_parser("list", help="List subject descriptors")
    list_p.add_argument(
        "--subjects-root",
        default=None,
        dest="subjects_root",
        help=(
            "Directory holding subject descriptors (*.toml/*.json). Required; the "
            "descriptors live in a separate evaluation-content checkout, e.g. "
            "--subjects-root /path/to/content-repo/evaluation/subjects"
        ),
    )
    list_p.set_defaults(func=cmd_subjects_list)

    show_p = subjects_sub.add_parser("show", help="Show normalized subject descriptor")
    show_p.add_argument("subject", help="Subject descriptor path (.toml or .json)")
    show_p.set_defaults(func=cmd_subjects_show)

    run_p = subjects_sub.add_parser("run", help="Run subject awareness matrix and write scorecard")
    run_p.add_argument("subject", help="Subject descriptor path (.toml or .json)")
    run_p.add_argument("--out-dir", default=None, dest="out_dir")
    run_p.add_argument("--out-json", default=None, dest="out_json")
    run_p.add_argument("--out-md", default=None, dest="out_md")
    run_p.add_argument("--model", default=DEFAULT_ANSWER_MODEL, help=argparse.SUPPRESS)
    run_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER,
                       choices=ANSWER_PROVIDERS)
    run_p.add_argument("--harness", default="arms-runner", help=argparse.SUPPRESS)
    run_p.add_argument("--context7-id", default=None, dest="context7_id")
    run_p.add_argument("--judge", action="store_true")
    run_p.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, dest="judge_model",
                       help=JUDGE_MODEL_HELP)
    run_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, dest="judge_provider",
                       choices=JUDGE_PROVIDERS)
    run_p.add_argument("--top-k", type=positive_int, default=5, dest="top_k")
    run_p.add_argument("--rerank-threshold", type=float, default=0.3, dest="rerank_threshold")
    run_p.add_argument("--max-iterations", type=positive_int, default=6, dest="max_iterations")
    run_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens",
                       help="Output-token ceiling per answer. Raise for reasoning models, which "
                            "can spend the provider default entirely on reasoning and return empty text")
    run_p.add_argument("--grounded-from-topics", action="store_true", dest="grounded_from_topics",
                       help="Let the judge use a question's expected_topics as ground truth when they "
                            "are substantive answer statements, enabling the factual_grounding "
                            "dimension for curated golden sets. Keyword-style topics are ignored")
    run_p.add_argument("--concurrency", type=positive_int, default=5)
    run_p.add_argument("--no-plugin-deltas", action="store_true", dest="no_plugin_deltas")
    run_p.add_argument(
        "--novelty-artifact", action="append", default=None, dest="novelty_artifact",
        metavar="PATH",
        help="Attach a novelty.v1 artifact from `novelty analyze` (repeatable, one per "
             "(artifact, model) pair). Reported per cell and never averaged: novelty is a property "
             "of a pair, and ranking artifacts by `added` inside one model correlated with the "
             "measured task delta at -0.446. All novelty checks are soft and never block",
    )
    run_p.add_argument(
        "--work-harnesses",
        default="",
        dest="work_harnesses",
        help=(
            "Comma-separated executable-task harnesses for suite.tasks "
            "(for example: codex,docker-claude-skill:/path/to/skills-repo/skills/<name>)."
        ),
    )
    run_p.add_argument(
        "--tasks-root",
        default=None,
        dest="tasks_root",
        help=(
            "Directory holding the task directories named in suite.tasks, e.g. "
            "<checkout>/evaluation/harbor/tasks. Without it, a suite.tasks entry has "
            "to be a full path to a task directory."
        ),
    )
    run_p.add_argument("--work-baseline-harness", default=None, dest="work_baseline_harness")
    run_p.add_argument("--work-model", default=None, dest="work_model")
    run_p.add_argument("--work-output-dir", default=None, dest="work_output_dir")
    run_p.add_argument("--work-command-template", default=None, dest="work_command_template")
    run_p.add_argument("--work-repeats", type=positive_int, default=1, dest="work_repeats")
    run_p.add_argument(
        "--work-dry-run",
        action="store_true",
        dest="work_dry_run",
        help="Build the task_runs artifact without invoking executable-task agents.",
    )
    run_p.add_argument(
        "--skip-work",
        action="store_true",
        dest="skip_work",
        help="Skip suite.tasks even when the subject descriptor declares them.",
    )
    run_p.set_defaults(func=cmd_subjects_run)

    evidence_p = subjects_sub.add_parser(
        "evidence",
        help="Render a subject scorecard as a skill_evidence.v1 document (no model calls)",
    )
    evidence_p.add_argument("scorecard", help="subject_scorecard JSON written by `subjects run`")
    evidence_p.add_argument("--out", default=None,
                            help="Output file (default: skill-evidence.json next to the scorecard)")
    evidence_p.set_defaults(func=cmd_subjects_evidence)


def _run_plan(descriptor, args: argparse.Namespace) -> tuple[Path, str, list[str]]:
    """The output dir, baseline arm name and per-product questions, or exit 2 when the descriptor cannot run.

    Checked before anything is written, so a descriptor that cannot run leaves no output dir behind."""
    from dokimi.subjects.loader import awareness_arm_specs

    try:
        out_dir = Path(args.out_dir) if args.out_dir else Path("results/subjects") / _safe_subject_id(descriptor.id)
        baseline_arm = _baseline_arm_name(descriptor.baseline, args)
        _validate_unique_awareness_arm_names(awareness_arm_specs(descriptor), args)
        questions = _questions_for_products(descriptor.suite.products, descriptor.suite.questions)
        _validate_unique_product_output_dirs(descriptor.suite.products)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    return out_dir, baseline_arm, questions


def _questions_for_products(products: tuple[str, ...], questions: tuple[str, ...]) -> list[str]:
    if len(questions) == len(products):
        return list(questions)
    if len(questions) == 1:
        return [questions[0] for _ in products]
    raise ValueError(
        "suite.questions must contain either one shared file or one file per suite.product"
    )


def _validate_unique_product_output_dirs(products: tuple[str, ...]) -> None:
    from dokimi.commands.arms import _safe_name

    safe_names = [_safe_name(product) for product in products]
    dot_names = [product for product, safe in zip(products, safe_names, strict=True) if safe in {".", ".."}]
    if dot_names:
        raise ValueError(
            "suite.products must not resolve to '.' or '..' output directories: "
            + ", ".join(dot_names)
        )
    collisions = sorted({name for name in safe_names if safe_names.count(name) > 1})
    if collisions:
        details = ", ".join(
            f"{name}: {', '.join(product for product in products if _safe_name(product) == name)}"
            for name in collisions
        )
        raise ValueError(f"suite.products collide after output directory sanitization: {details}")


def _validate_unique_awareness_arm_names(arm_specs: list[str], args: argparse.Namespace) -> None:
    from dokimi.treatments import create_treatments

    try:
        create_treatments(
            arm_specs,
            top_k=args.top_k,
            rerank_threshold=args.rerank_threshold,
        )
    except ValueError as exc:
        if "Duplicate arm name" not in str(exc):
            raise
        raise ValueError(
            "subject awareness arms resolve to duplicate runtime names; "
            "use a baseline with a distinct treatment kind or split this comparison"
        ) from exc


def _safe_subject_id(subject_id: str) -> str:
    from dokimi.commands.arms import _safe_name

    safe = _safe_name(subject_id)
    if safe in {".", ".."}:
        raise ValueError("subject.id must not resolve to '.' or '..' output directory")
    return safe


def _matrix_config_for_run(descriptor, args: argparse.Namespace) -> dict:
    """Build matrix config, honoring CLI model/provider when descriptor omitted cells."""
    from dokimi.subjects.loader import matrix_config

    if descriptor.matrix_cells_explicit:
        return matrix_config(descriptor)
    return {
        "matrix": {
            "cells": [{
                "id": "arms-runner",
                "model": args.model,
                "provider": args.provider,
                "harness": args.harness,
                "plugins": [],
            }]
        }
    }


def _baseline_arm_name(baseline_spec: str, args: argparse.Namespace) -> str:
    """Resolve a baseline treatment spec to its runtime arm name."""
    from dokimi.treatments import create_treatments

    treatment = create_treatments(
        [baseline_spec],
        top_k=args.top_k,
        rerank_threshold=args.rerank_threshold,
    )[0]
    return treatment.name


def _run_subject_work(descriptor, args: argparse.Namespace, *, out_dir: Path, tasks: list | None = None) -> dict | None:
    """Run executable task suites for a subject when explicitly requested."""
    if not descriptor.suite.tasks:
        return None
    if args.skip_work:
        return {
            "status": "skipped",
            "tasks": list(descriptor.suite.tasks),
            "arm_specs": _work_arm_specs(descriptor),
            "warnings": ["suite.tasks skipped by --skip-work"],
        }

    harnesses = [h.strip() for h in args.work_harnesses.split(",") if h.strip()]
    tasks = tasks if tasks is not None else _validate_subject_work_args(descriptor, args)
    if args.work_baseline_harness and args.work_baseline_harness not in harnesses:
        harnesses.insert(0, args.work_baseline_harness)

    from dokimi.artifacts import save_artifact
    from dokimi.harnesses import PreflightError, TaskSuiteRunner
    from dokimi.report.task_runs_report import render_task_runs_report

    work_dir = Path(args.work_output_dir) if args.work_output_dir else out_dir / "work"
    task_runs_path = work_dir / "task-runs.json"
    runner = TaskSuiteRunner(
        tasks=tasks,
        harnesses=harnesses,
        model=args.work_model or args.model,
        baseline_harness=args.work_baseline_harness,
        output_root=work_dir,
        command_template=args.work_command_template,
        dry_run=args.work_dry_run,
        repeats=args.work_repeats,
    )
    try:
        runner.validate_harnesses()
    except ValueError as exc:
        usage_exit(str(exc))
    try:
        artifact = runner.run()
    except PreflightError as exc:
        raise SystemExit(f"Error: {exc}") from None
    task_runs_md = task_runs_path.with_suffix(".md")
    with exit_on_unwritable(task_runs_path):
        save_artifact("task_runs", artifact, task_runs_path)
    with exit_on_unwritable(task_runs_md):
        task_runs_md.parent.mkdir(parents=True, exist_ok=True)
        task_runs_md.write_text(render_task_runs_report(artifact), encoding="utf-8")
    return {
        "status": "run",
        "tasks": list(descriptor.suite.tasks),
        "arm_specs": _work_arm_specs(descriptor),
        "harnesses": harnesses,
        "task_runs": str(task_runs_path),
        "task_report": str(task_runs_md),
        "summary": artifact.get("summary", {}),
    }


def _work_arm_specs(descriptor) -> list[str]:
    from dokimi.subjects.loader import work_arm_specs

    return work_arm_specs(descriptor)


def _validate_subject_work_args(descriptor, args: argparse.Namespace) -> list:
    if not descriptor.suite.tasks or args.skip_work:
        return []
    harnesses = [h.strip() for h in args.work_harnesses.split(",") if h.strip()]
    if not harnesses:
        raise ValueError(
            "suite.tasks declared; pass --work-harnesses <harness[,harness]> "
            "to run them, or --skip-work to leave work.status=skipped"
        )
    from dokimi.harnesses import load_task

    root = getattr(args, "tasks_root", None)
    return [load_task(task, root=Path(root) if root else None) for task in descriptor.suite.tasks]
