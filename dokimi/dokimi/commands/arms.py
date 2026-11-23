# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""arms subcommand group: run (N-way treatment comparison)."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dokimi import cli_flags
from dokimi.commands.novelty_budget import fresh_partial_destination
from dokimi.exit_codes import EXIT_FAILURE, EXIT_USAGE
from dokimi.llm import ANSWER_PROVIDERS
from dokimi.utils import InputError, question_id
from dokimi.write_probe import can_write
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_MODEL_FOR_PROVIDER,
    JUDGE_MODEL_HELP,
)
from dokimi.eval.question_text import EmptyQuestionError

if TYPE_CHECKING:
    from dokimi.eval.arms_run import ArmsRunConfig


def run_config_from_args(args: argparse.Namespace, **overrides) -> ArmsRunConfig:
    """The run configuration in parsed *args*: the one place flags become an ``ArmsRunConfig``.

    A config field the command has no flag for takes the config's default, the value
    ``arms run`` runs with when that flag is left out. Pass *overrides* for fields the
    command sets itself.
    """
    from dataclasses import fields

    from dokimi.eval.arms_run import ArmsRunConfig

    values = {f.name: getattr(args, f.name) for f in fields(ArmsRunConfig) if hasattr(args, f.name)}
    return ArmsRunConfig(**{**values, **overrides})


def judge_panel_roles(value: str) -> str:
    """argparse type for --judge-panel: an unknown role fails before any answer is paid for."""
    from dokimi.eval.panel import JUDGE_ROLES

    unknown = [r.strip() for r in value.split(",") if r.strip() and r.strip() not in JUDGE_ROLES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown role(s) {', '.join(unknown)}; available: {', '.join(JUDGE_ROLES)}")
    return value


def _exit_if_terminated(output: dict) -> None:
    """A run-wide stop: the artifact is saved and says what stopped, and the run exits 1."""
    term = output.get("terminated")
    if term:
        print(f"Error running arms: stopped after {term['questions_recorded']} of "
              f"{term['questions_requested']} questions: {term['reason']}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)


def cmd_arms_run(args: argparse.Namespace) -> None:
    """Run an N-arm treatment comparison and optionally judge it."""
    from dokimi.eval.arms_run import LibraryIdError, ProbeError, prepare_arms, run_prepared

    # Setup (inputs, arms, plugins, sampling, judge) exits 2; a failed sampling probe is a run failure (1).
    try:
        specs = _parse_arm_specs(args.arms)
        questions = _load_questions(args.questions)
        config = _with_named_cell(run_config_from_args(args), args.matrix_cells, args.matrix_cell)
        prepared = prepare_arms(config, specs, questions)
    except EmptyQuestionError:
        raise  # a bad input record, not an arm setup error: cli.main reports it (exit 2)
    except ProbeError as exc:
        print(f"Error running arms: {exc}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)
    except LibraryIdError as exc:
        raise InputError(str(exc)) from exc
    except (ValueError, OSError, RecursionError) as exc:
        raise InputError(f"cannot build arms/plugins: {exc}") from exc
    out_json = Path(args.out_json) if args.out_json else Path(f"results/arms/{args.product}.json")
    out_md = Path(args.out_md) if args.out_md else Path(f"results/arms/{args.product}.md")
    partial = _Partial(out_json)
    partial.name()  # an --out whose partial file cannot be named is refused before any answer is paid for
    try:
        output = run_prepared(prepared, config, questions, on_partial=partial.save)
    except EmptyQuestionError:
        raise  # a bad input record, not a run error: cli.main reports it (exit 2)
    except (ValueError, OSError, RecursionError) as exc:
        print(f"Error running arms: {exc}{partial.note()}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)

    _save_arms_output(output, out_json, out_md, partial)

    if output.get("summary", {}).get("per_arm"):
        print("\nSummary (avg aggregate):")
        for arm, stats in output["summary"]["per_arm"].items():
            avg = stats.get("avg_aggregate")
            delta = stats.get("delta_vs_baseline")
            avg_s = "n/a" if avg is None else f"{avg:.1f}"
            delta_s = "" if (delta is None or arm == args.baseline_arm) else f" (delta {delta:+.1f})"
            if stats.get("delta_error") and arm != args.baseline_arm:
                delta_s = f" (delta not computed: {stats['delta_error']})"
            err_s = f" ({stats['errors']} errors)" if stats.get("errors") else ""
            print(f"  {arm:<24} {avg_s}{delta_s}{err_s}")
    _exit_if_terminated(output)


def cmd_arms_matrix_run(args: argparse.Namespace) -> None:
    """Run every cell in a matrix.cells descriptor and write a rollup."""
    out_dir = Path(args.out_dir) if args.out_dir else Path(f"results/arms/{args.product}-matrix")
    run_matrix(
        run_config_from_args(args), arms=args.arms, questions=args.questions,
        matrix_cells=args.matrix_cells, out_dir=out_dir,
        out_json=Path(args.out_json) if args.out_json else None,
        out_md=Path(args.out_md) if args.out_md else None,
        plugin_deltas=args.plugin_deltas,
    )


def _load_matrix_inputs(config: ArmsRunConfig, arms: str, questions: str, matrix_cells: str):
    """The arms, questions and cells of a matrix run, checked before any cell is set up. A refused
    input exits 2 (a CLI cell refusal is an ``InputError``, reported by cli.main); a failed
    sampling probe, a real call, exits 1."""
    from dokimi.eval import arms_run
    from dokimi.eval.cells import load_matrix_cells

    try:
        specs = _parse_arm_specs(arms)
        loaded_questions = _load_questions(questions)
        cells = load_matrix_cells(matrix_cells)
        _validate_unique_cell_filenames(cells)
        arms_run.check_matrix(config, cells, specs)
    except InputError:  # EmptyQuestionError is one
        raise  # a bad input record or a refused cell: cli.main reports it (exit 2)
    except arms_run.ProbeError as exc:
        print(f"Error loading matrix run inputs: {exc}", file=sys.stderr)
        sys.exit(EXIT_FAILURE)
    except (ValueError, OSError, RecursionError) as exc:
        raise InputError(f"cannot load matrix run inputs: {exc}") from exc
    return specs, loaded_questions, cells


def _check_cells_dir(cells_dir: Path) -> None:
    """Exit 2 when *cells_dir* cannot be made: the nearest existing ancestor must be a directory
    the process can write to. It tries the write in that directory (in a directory made there when *cells_dir* is
    missing) and removes what it made; only a directory that refuses the removal keeps a ``.write-probe-*`` entry.
    An existing parent of *cells_dir* must also take a file, since the rollup is written there."""
    # A dangling symlink exists as a link but not as a target: it is the nearest entry, and it is refused.
    existing = next((p for p in (cells_dir, *cells_dir.parents) if p.exists() or p.is_symlink()), None)
    if existing is None:  # no ancestor exists at all (an unmounted drive): nothing can be made
        print(f"Error: cannot write {cells_dir}: no existing directory above it", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    # The rollup and the plugin deltas are files in the parent (the out dir), which takes a folder-only grant no better.
    out_dir = cells_dir.parent
    refused = None
    if not existing.is_dir() or not can_write(existing, make_dir=existing != cells_dir):
        refused = existing
    elif out_dir.is_dir() and not can_write(out_dir):
        refused = out_dir
    if refused is not None:
        print(f"Error: cannot write {cells_dir}: {refused} is not a writable directory", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _make_cells_dir(cells_dir: Path) -> None:
    """Make *cells_dir*, or exit 2. The walk above predicts mkdir lexically, so a '..' after a
    missing component passes it; making the dir before the first answer is exact."""
    try:
        cells_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"Error: cannot write {cells_dir}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def run_matrix(config: ArmsRunConfig, *, arms: str, questions: str, matrix_cells: str,
               out_dir: Path, out_json: Path | None = None, out_md: Path | None = None,
               plugin_deltas: bool = True) -> None:
    """``arms matrix-run`` from a config: run every cell of *matrix_cells* and write the cells,
    the plugin deltas and the rollup.

    *out_json* defaults to ``matrix-rollup.json`` in *out_dir*, *out_md* to *out_json* with a
    ``.md`` suffix.
    """
    from dokimi.eval import arms_run
    from dokimi.eval.matrix_rollup import build_matrix_rollup, strip_internal_payloads
    from dokimi.report.matrix_rollup_report import render_matrix_rollup_report
    from dokimi.report.plugin_delta_report import render_plugin_delta_report

    specs, loaded_questions, cells = _load_matrix_inputs(config, arms, questions, matrix_cells)

    # The cells dir is made once every cell is set up, so a cell refused in setup writes nothing.
    # An uncreatable one is refused here and again by the mkdir, before any answer is paid for.
    cells_dir = out_dir / "cells"
    _check_cells_dir(cells_dir)
    cell_artifacts = []

    partials: dict[str, _Partial] = {}

    def before_run() -> None:  # once every cell is set up; each cell names its partial file at its first save
        _make_cells_dir(cells_dir)
        partials.update((cell.name, _Partial(cells_dir / f"{_safe_name(cell.name)}.json")) for cell in cells)

    def save_cell(cell, output: dict) -> None:
        safe_name = _safe_name(cell.name)
        cell_json = cells_dir / f"{safe_name}.json"
        _save_arms_output(output, cell_json, cells_dir / f"{safe_name}.md", partials.get(cell.name))
        cell_artifacts.append((cell.name, cell_json, output))

    try:
        arms_run.run_matrix(config, cells, specs, loaded_questions, on_cell=save_cell,
                            before_run=before_run,
                            on_partial=lambda cell, out: partials[cell.name].save(out))
    except EmptyQuestionError:
        raise  # a bad input record, not a cell error: cli.main reports it (exit 2)
    except arms_run.LibraryIdError as exc:
        raise InputError(str(exc)) from exc
    except arms_run.MatrixCellError as exc:
        verb = "building" if exc.usage else "running"
        kept = partials[exc.cell].note() if exc.cell in partials else ""
        print(f"Error {verb} matrix cell {exc.cell!r}: {exc}{kept}", file=sys.stderr)
        sys.exit(EXIT_USAGE if exc.usage else EXIT_FAILURE)

    rollup = build_matrix_rollup(
        library_name=config.product,
        cell_artifacts=cell_artifacts,
        out_dir=out_dir,
        compute_plugin_deltas=plugin_deltas,
    )
    for row in rollup.get("plugin_deltas", []):
        payload = row.get("_artifact_payload")
        if not payload:
            continue
        delta_json = Path(row["artifact"])
        _save_artifact_or_exit("plugin_delta", payload, delta_json)
        _write_report(delta_json.with_suffix(".md"), render_plugin_delta_report(payload))
        print(f"OK Saved plugin delta:    {delta_json}")

    persisted = strip_internal_payloads(rollup)
    out_json = out_json or out_dir / "matrix-rollup.json"
    out_md = out_md or out_json.with_suffix(".md")
    _save_artifact_or_exit("matrix_rollup", persisted, out_json)
    _write_report(out_md, render_matrix_rollup_report(persisted))
    print(f"OK Saved matrix rollup:   {out_json}")
    print(f"OK Saved matrix report:   {out_md}")


def cmd_arms_plugin_delta(args: argparse.Namespace) -> None:
    """Compare paired no-plugin/plugin arms artifacts."""
    from dokimi.artifacts import load_artifact
    from dokimi.eval.plugin_delta import PluginDeltaError, compare_plugin_runs
    from dokimi.records import records
    from dokimi.report.plugin_delta_report import render_plugin_delta_report

    try:
        baseline = load_artifact("arms", Path(args.baseline_json))
        plugin = load_artifact("arms", Path(args.plugin_json))
        for path, artifact in ((args.baseline_json, baseline), (args.plugin_json, plugin)):
            for section in ("answers", "evaluations"):
                records(artifact.get(section) or [], source=f"{path} {section}", kind=section)
        output = compare_plugin_runs(baseline, plugin)
    except (PluginDeltaError, ValueError, OSError, RecursionError) as exc:
        raise InputError(f"cannot compute plugin delta: {exc}") from exc

    out_json = Path(args.out_json) if args.out_json else Path("results/arms/plugin_delta.json")
    _save_artifact_or_exit("plugin_delta", output, out_json)
    print(f"OK Saved plugin delta: {out_json}")

    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    _write_report(out_md, render_plugin_delta_report(output))
    print(f"OK Saved plugin delta report: {out_md}")

    print(
        f"Plugin delta: {output['baseline_plugin_set']} -> {output['plugin_set']} "
        f"({output['harness']}, {output['provider']}/{output['model']})"
    )
    for arm, row in output.get("score_deltas", {}).items():
        delta = row.get("aggregate_delta")
        delta_s = "n/a" if delta is None else f"{delta:+.1f}"
        print(f"  {arm:<24} judge delta {delta_s}, n={row.get('n', 0)}")


def register(sub, positive_int) -> None:
    """Add the `arms` subcommand group."""
    arms_p = sub.add_parser(
        "arms",
        help="Compare context-augmentation treatments (docs, MCP, skills, agent profiles)",
    )
    arms_sub = arms_p.add_subparsers(dest="arms_cmd", required=True)

    arms_run_p = arms_sub.add_parser(
        "run",
        help="Run an N-arm comparison and (optionally) judge it",
    )
    arms_run_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    arms_run_p.add_argument("--questions", required=True, help="Path to questions JSON file")
    arms_run_p.add_argument(
        "--arms", required=True,
        help="Comma-separated arm specs. Examples: "
             "'baseline,docs', 'baseline,docs:local:./docs,profile:data/agent_profiles/concise_expert.md', "
             "'baseline,mcp:http=https://mcp.context7.com/mcp,skill:/path/to/skills-repo/skills/<name>'",
    )
    arms_run_p.add_argument("--model", default=DEFAULT_ANSWER_MODEL, help="LLM for answering")
    arms_run_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER,
                            choices=ANSWER_PROVIDERS)
    arms_run_p.add_argument("--harness", default="arms-runner",
                            help="Execution harness label stamped into output (default: arms-runner)")
    arms_run_p.add_argument("--plugins", default="",
                            help="Comma-separated plugin refs, e.g. 'plugin:caveman:ultra' or 'plugin:truncate:800'")
    arms_run_p.add_argument("--matrix-cells", default=None, dest="matrix_cells",
                            help="JSON file containing matrix.cells descriptors")
    arms_run_p.add_argument("--matrix-cell", default=None, dest="matrix_cell",
                            help="Named matrix cell to run from --matrix-cells")
    arms_run_p.add_argument("--context7-id", default=None, dest="context7_id",
                            help="Explicit library id for doc/MCP arms (skips resolution)")
    arms_run_p.add_argument("--baseline-arm", default="baseline", dest="baseline_arm",
                            help="Arm name used as the delta baseline (default: baseline)")
    arms_run_p.add_argument("--stats-correction", default="holm", dest="stats_correction",
                            # Kept literal so registering the CLI stays stdlib-only;
                            # eval.stats.CORRECTIONS is the enforced source of truth
                            # and ArmRunner validates against it.
                            choices=["holm", "bh"],
                            help="Multiple-comparison correction across treatment arms: "
                                 "'holm' (Holm-Bonferroni, controls family-wise error, "
                                 "default) or 'bh' (Benjamini-Hochberg, controls false "
                                 "discovery rate)")
    arms_run_p.add_argument("--judge", action="store_true",
                            help="Also score each arm with the LLM-as-judge")
    arms_run_p.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, dest="judge_model",
                            help=JUDGE_MODEL_HELP)
    arms_run_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, dest="judge_provider",
                            choices=JUDGE_PROVIDERS)
    _add_judge_context(arms_run_p)
    arms_run_p.add_argument("--top-k", type=positive_int, default=5, dest="top_k",
                            help="Docs to retrieve before reranking, for doc/MCP arms (default: 5)")
    arms_run_p.add_argument("--rerank-threshold", type=float, default=0.3, dest="rerank_threshold")
    arms_run_p.add_argument("--max-iterations", type=positive_int, default=6, dest="max_iterations",
                            help="Max tool-call rounds for agentic arms (agent:/skill-agent:, default: 6)")
    cli_flags.add(arms_run_p, "tokens", "--max-output-tokens", metavar="N", type=positive_int, default=None, dest="max_tokens",
                  help="Output-token ceiling per answer. Raise for reasoning models, which "
                  "can spend the provider default entirely on reasoning and return empty text")
    arms_run_p.add_argument("--grounded-from-topics", action="store_true", dest="grounded_from_topics",
                            help="Let the judge use a question's expected_topics as ground truth when they "
                                 "are substantive answer statements, enabling the factual_grounding "
                                 "dimension for curated golden sets. Keyword-style topics are ignored")
    arms_run_p.add_argument("--judge-temperature", type=float, default=None,
                        dest="judge_temperature",
                        help="Sampling temperature for the JUDGE. Separate from "
                             "--temperature, which shapes answer generation; grading "
                             "is its own sampled call and its own noise source.")
    arms_run_p.add_argument("--temperature", type=float, default=None,
                            help="Sampling temperature for answer generation. Omitted by "
                                 "default (provider default applies). Pin it to reduce "
                                 "run-to-run score variance.")
    arms_run_p.add_argument("--seed", type=int, default=None,
                            help="Sampling seed for answer generation, where the provider "
                                 "honours one. Best-effort reproducibility.")
    arms_run_p.add_argument("--judge-panel", default="", dest="judge_panel", type=judge_panel_roles,
                            help="Comma-separated judge roles (e.g. "
                                 "'technical_expert,developer_advocate,doc_reviewer'). When "
                                 "set, --judge scores each answer with a panel and records "
                                 "grader spread and agreement instead of one opinion.")
    arms_run_p.add_argument("--session-turns", type=positive_int, default=None, dest="session_turns",
                            help="Measure multi-turn sessions: each question is asked as its question "
                                 "followed by the first N-1 entries of its 'follow_ups' list, with the "
                                 "history kept, and each turn's token and cache usage recorded. Non-agentic "
                                 "arms only. Measurement only: refused with --judge")
    arms_run_p.add_argument("--turn-reminder", default=None, dest="turn_reminder",
                            help="With --session-turns, also run each arm as '<arm>+reminder', with this "
                                 "text appended to every user turn in a <system-reminder> block")
    arms_run_p.add_argument("--concurrency", type=positive_int, default=5)
    cli_flags.add(arms_run_p, "output", "--out", default=None, dest="out_json",
                  help="Output JSON path (default: results/arms/{product}.json)")
    arms_run_p.add_argument("--out-md", default=None, dest="out_md",
                            help="Output Markdown report path (default: results/arms/{product}.md)")
    arms_run_p.set_defaults(func=cmd_arms_run)

    matrix_run_p = arms_sub.add_parser(
        "matrix-run",
        help="Run every cell from a matrix.cells descriptor and write a rollup",
    )
    _add_common_run_args(matrix_run_p, positive_int, matrix_required=True)
    matrix_run_p.add_argument("--out-dir", default=None, dest="out_dir",
                              help="Directory for per-cell artifacts (default: results/arms/{product}-matrix)")
    cli_flags.add(matrix_run_p, "output", "--out", default=None, dest="out_json",
                  help="Matrix rollup JSON path")
    matrix_run_p.add_argument("--out-md", default=None, dest="out_md",
                              help="Matrix rollup Markdown path")
    matrix_run_p.add_argument("--no-plugin-deltas", action="store_false", dest="plugin_deltas",
                              help="Do not compute paired plugin_delta artifacts")
    matrix_run_p.set_defaults(func=cmd_arms_matrix_run, plugin_deltas=True)

    plugin_delta_p = arms_sub.add_parser(
        "plugin-delta",
        help="Compare paired arms artifacts that differ only by plugin set",
    )
    plugin_delta_p.add_argument(
        "--baseline-json", required=True, dest="baseline_json",
        help="arms.v1 artifact for the baseline/no-plugin cell",
    )
    plugin_delta_p.add_argument(
        "--plugin-json", required=True, dest="plugin_json",
        help="arms.v1 artifact for the plugin-enabled cell",
    )
    cli_flags.add(plugin_delta_p, "output", "--out", default=None, dest="out_json")
    plugin_delta_p.add_argument("--out-md", default=None, dest="out_md")
    plugin_delta_p.set_defaults(func=cmd_arms_plugin_delta)


def _add_judge_context(parser) -> None:
    # Literal choices, as the stdlib-only CLI registration needs; ArmRunner checks the same values.
    cli_flags.add(parser, "judge-context", "--judge-context", default="arm", dest="judge_context",
                  choices=["arm", "baseline"],
                  help="What the judge is shown about each arm's treatment material. arm (default): the "
                       "arm's own material. baseline: no material on any arm, the context a baseline arm's "
                       "judge sees, so every arm is judged on one context. Answers are unchanged")


def _add_common_run_args(parser, positive_int, *, matrix_required: bool = False) -> None:
    parser.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    parser.add_argument("--questions", required=True, help="Path to questions JSON file")
    parser.add_argument(
        "--arms", required=True,
        help="Comma-separated arm specs. Examples: "
             "'baseline,docs', 'baseline,docs:local:./docs,profile:data/agent_profiles/concise_expert.md', "
             "'baseline,mcp:http=https://mcp.context7.com/mcp,skill:/path/to/skills-repo/skills/<name>'",
    )
    parser.add_argument("--model", default=DEFAULT_ANSWER_MODEL, help="LLM for answering")
    parser.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER,
                        choices=ANSWER_PROVIDERS)
    parser.add_argument("--harness", default="arms-runner",
                        help="Execution harness label stamped into output (default: arms-runner)")
    parser.add_argument("--plugins", default="",
                        help="Comma-separated plugin refs, e.g. 'plugin:caveman:ultra' or 'plugin:truncate:800'")
    parser.add_argument("--matrix-cells", required=matrix_required, default=None, dest="matrix_cells",
                        help="JSON file containing matrix.cells descriptors")
    if not matrix_required:
        parser.add_argument("--matrix-cell", default=None, dest="matrix_cell",
                            help="Named matrix cell to run from --matrix-cells")
    else:
        parser.set_defaults(matrix_cell=None)
    parser.add_argument("--context7-id", default=None, dest="context7_id",
                        help="Explicit library id for doc/MCP arms (skips resolution)")
    parser.add_argument("--baseline-arm", default="baseline", dest="baseline_arm",
                        help="Arm name used as the delta baseline (default: baseline)")
    parser.add_argument("--stats-correction", default="holm", dest="stats_correction",
                        # Kept literal so registering the CLI stays stdlib-only;
                        # eval.stats.CORRECTIONS is the enforced source of truth
                        # and ArmRunner validates against it.
                        choices=["holm", "bh"],
                        help="Multiple-comparison correction across treatment arms: "
                             "'holm' (Holm-Bonferroni, controls family-wise error, default) "
                             "or 'bh' (Benjamini-Hochberg, controls false discovery rate)")
    parser.add_argument("--judge", action="store_true",
                        help="Also score each arm with the LLM-as-judge")
    parser.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, dest="judge_model",
                        help=JUDGE_MODEL_HELP)
    parser.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, dest="judge_provider",
                        choices=JUDGE_PROVIDERS)
    _add_judge_context(parser)
    parser.add_argument("--top-k", type=positive_int, default=5, dest="top_k",
                        help="Docs to retrieve before reranking, for doc/MCP arms (default: 5)")
    parser.add_argument("--rerank-threshold", type=float, default=0.3, dest="rerank_threshold")
    parser.add_argument("--max-iterations", type=positive_int, default=6, dest="max_iterations",
                        help="Max tool-call rounds for agentic arms (agent:/skill-agent:, default: 6)")
    cli_flags.add(parser, "tokens", "--max-output-tokens", metavar="N", type=positive_int, default=None, dest="max_tokens",
                  help="Output-token ceiling per answer. Raise for reasoning models, which "
                  "can spend the provider default entirely on reasoning and return empty text")
    parser.add_argument("--grounded-from-topics", action="store_true", dest="grounded_from_topics",
                        help="Let the judge use a question's expected_topics as ground truth when they "
                             "are substantive answer statements, enabling the factual_grounding "
                             "dimension for curated golden sets. Keyword-style topics are ignored")
    parser.add_argument("--judge-temperature", type=float, default=None,
                        dest="judge_temperature",
                        help="Sampling temperature for the JUDGE. Separate from "
                             "--temperature, which shapes answer generation; grading "
                             "is its own sampled call and its own noise source.")
    parser.add_argument("--temperature", type=float, default=None,
                        help="Sampling temperature for answer generation. Omitted by "
                             "default (provider default applies). Pin it to reduce "
                             "run-to-run score variance.")
    parser.add_argument("--seed", type=int, default=None,
                        help="Sampling seed for answer generation, where the provider "
                             "honours one. Best-effort reproducibility.")
    parser.add_argument("--judge-panel", default="", dest="judge_panel", type=judge_panel_roles,
                        help="Comma-separated judge roles (e.g. "
                             "'technical_expert,developer_advocate,doc_reviewer'). When "
                             "set, --judge scores each answer with a panel and records "
                             "grader spread and agreement instead of one opinion.")
    parser.add_argument("--concurrency", type=positive_int, default=5)


def _parse_arm_specs(raw: str) -> list[str]:
    from dokimi.treatments.specs import split_arm_list

    return split_arm_list(raw)


def _load_questions(path: str) -> list[dict]:
    """The question set at ``path`` as questions.v2 records, each with an id of its own.

    Read by :func:`dokimi.questions.artifact.load_questions`, which gives an id-less record its
    positional ``q0000`` (``_``-prefixed past any id already in the set). Every question needs
    its own id: the judge's context is keyed by it, and the arm runner falls back to
    ``"unknown_q"``; with a shared key the judge cannot tell which question's treatment
    material belongs to which answer. The reader refuses duplicated ids, since there is no right
    answer to which question they mean.
    """
    from dokimi.questions.artifact import load_questions

    _refuse_external_trigger_set(path)
    questions = load_questions(path).questions
    ids = [question_id(q) for q in questions]
    for i, qid in enumerate(ids):
        # The records reader refuses any other id, so a run with one would be paid for and lost.
        if qid is not None and (isinstance(qid, bool) or not isinstance(qid, (str, int))):
            raise ValueError(f"{path}: questions[{i}]: id must be a string or an integer, got {qid!r}")
    print(f"Loaded {len(questions)} questions from {path}")
    return questions


def _refuse_external_trigger_set(path: str) -> None:
    """Point a skill-reach or skill-creator trigger set at the command that reads it.

    Those sets are read through ``eval triggers generate --trigger-set``, which also
    lints them against the catalog the arm will offer; a questions loader that
    guessed at them would drop that lint and, for skill-creator, the target skill.
    """
    import json

    from dokimi.external.trigger_set import FORMAT_QUESTIONS, detect_trigger_format

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return  # the questions loader reports it
    fmt = detect_trigger_format(data)
    if fmt and fmt != FORMAT_QUESTIONS:
        target = "=<skill>" if fmt == "skill-creator" else ""
        raise ValueError(
            f"{path} is a {fmt} trigger set, not a questions set; convert it with "
            f"`dokimi eval triggers generate --catalog <skills> --trigger-set {path}{target} "
            f"--format questions --out q.json`, then run the skill-catalog: arm on q.json")


def _with_named_cell(config: ArmsRunConfig, matrix_cells: str | None,
                     matrix_cell: str | None) -> ArmsRunConfig:
    """*config* as the cell named by ``--matrix-cell`` in ``--matrix-cells`` runs it, if one is."""
    from dokimi.eval.arms_run import cell_config, validate_supported_matrix_harnesses
    from dokimi.eval.cells import load_matrix_cells, select_matrix_cell

    if not (matrix_cells or matrix_cell):
        return config
    if not matrix_cells or not matrix_cell:
        raise ValueError("--matrix-cells and --matrix-cell must be passed together")
    from dokimi.eval.arms_refusals import refuse_cli_cell_model

    cell = select_matrix_cell(load_matrix_cells(matrix_cells), matrix_cell)
    refuse_cli_cell_model(cell)  # the parse-time model gate read only --model, not the cell's
    validate_supported_matrix_harnesses([cell])
    return cell_config(config, cell)


def _write_report(path: Path, text: str) -> None:
    """Write a Markdown report, or exit 1 naming ``path`` when it cannot be written."""
    from dokimi.commands._output import write_report_text

    try:
        write_report_text(path, text)
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


def _save_artifact_or_exit(kind: str, payload: dict, path: Path) -> None:
    """``save_artifact``, or exit 2 naming ``path`` when it cannot be written."""
    from dokimi.artifacts import save_artifact

    try:
        save_artifact(kind, payload, path)
    except OSError as exc:
        print(f"Error: cannot write {path}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)


class _Partial:
    """The file a run keeps its answered questions in until it saves *out_json* whole (#228 step 4).

    ``<stem>.partial.json`` beside it, or the next name nothing holds: never a finished file, nor the
    checkpoint of an earlier run that crashed. It is reserved (created) at the first save, so a matrix cell
    passes over the files the cells before it finished, and a run running at the same time takes another name.
    """

    def __init__(self, out_json: Path) -> None:
        self.out_json = out_json
        self._path: Path | None = None
        self._created = False
        self.recorded = 0
        self.failed = False

    def name(self) -> Path:
        """The name the file would take now; ``InputError`` when none can be named. Reserved at the first save."""
        if self._path is not None:
            return self._path
        try:
            return fresh_partial_destination(self.out_json)
        except OSError as exc:
            raise InputError(f"cannot write {self.out_json}: {exc}") from exc

    def _reserve(self) -> Path:
        """Create the file before its first save, so a run started meanwhile with the same stem takes another name."""
        with contextlib.suppress(OSError):  # a directory that cannot be made: the save names the refusal
            self.out_json.parent.mkdir(parents=True, exist_ok=True)  # else a missing one would reserve nothing
        aliases = []  # names the file system takes for out's own file; kept until the next name is found
        while self._path is None:
            path = self.name()
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666))
            except FileExistsError:  # another run took it since: the next name
                continue
            except OSError:  # not created: the save that follows fails on it and names the refusal
                self._created = False
            else:
                if _same_file(self.out_json, path):  # a folding the name key missed: out's link reaches it now
                    aliases.append(path)
                    continue
                self._created = True
            self._path = path
        for alias in aliases:
            alias.unlink(missing_ok=True)
        return self._path

    def save(self, output: dict) -> None:
        if self.failed:  # the run is stopping on the first failure: the questions still in flight add no message
            return
        try:
            _save_artifact_or_exit("arms", output, self._reserve())
        except BaseException:
            self.failed = True
            if self._created and not self.recorded:  # the empty file the reservation made holds nothing
                with contextlib.suppress(OSError):
                    self.name().unlink(missing_ok=True)
            raise
        self.recorded = output["partial"]["questions_recorded"]

    def drop(self) -> None:
        """Remove the file once the run is saved whole; one this run did not write (an earlier run's) stays."""
        if not self.recorded:
            return
        self.recorded = 0
        path = self.name()
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:  # the run is saved whole; the stale partial file only takes space
            print(f"Warning: could not remove {path}: {exc}", file=sys.stderr)

    def note(self) -> str:
        """Where the questions this run answered went, for the message of a run that failed before its save."""
        return f" ({self.recorded} answered questions are kept in {self.name()})" if self.recorded else ""


def _same_file(a: Path, b: Path) -> bool:
    """Whether *a* and *b* are one file; ``False`` when either cannot be found."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _save_arms_output(output: dict, out_json: Path, out_md: Path, partial: _Partial | None = None) -> None:
    """Save the run whole, then drop *partial*, the file it kept while answering."""
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.report.arms_report import render_arms_report

    try:
        ArmRunner.save(output, out_json)
    except OSError as exc:
        kept = "" if partial is None else partial.note()
        print(f"Error: cannot write {out_json}: {exc}{kept}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    print(f"OK Saved arms comparison: {out_json}")
    if partial is not None:
        partial.drop()
    _write_report(out_md, render_arms_report(output))
    print(f"OK Saved arms report:     {out_md}")


def _validate_unique_cell_filenames(cells) -> None:
    """Reject matrix cells that would overwrite each other's artifacts."""
    seen: dict[str, str] = {}
    for cell in cells:
        safe_name = _safe_name(cell.name)
        if safe_name in seen:
            raise ValueError(
                "matrix cell names collide after filename sanitization: "
                f"{seen[safe_name]!r} and {cell.name!r} both map to {safe_name!r}"
            )
        seen[safe_name] = cell.name


def _safe_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "-" for c in name)
    return safe.strip("-") or "cell"
