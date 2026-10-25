# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""arms subcommand group: run (N-way treatment comparison)."""

from __future__ import annotations

import argparse
import functools
from collections import Counter
import sys
from pathlib import Path

from dokimi.utils import question_id
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_MODEL_FOR_PROVIDER,
    JUDGE_MODEL_HELP,
)

# Run-configuration fields the arms commands read off parsed args, shared with
# any other command that drives an arms run by constructing its own Namespace.
# Kept in one place so a new flag reaches every caller: enumerating these at each
# call site is what lets a flag silently fall back to its default, since
# _build_arms_output reads them with getattr defaults and cannot tell a caller
# that omitted a field from a user who chose the default.
FORWARDED_RUN_CONFIG_FIELDS = (
    "model",
    "provider",
    "harness",
    "context7_id",
    "judge",
    "judge_model",
    "judge_provider",
    "top_k",
    "rerank_threshold",
    "max_iterations",
    "max_tokens",
    "grounded_from_topics",
    "temperature",
    "seed",
    "judge_temperature",
    "judge_panel",
    "concurrency",
    "stats_correction",
    "session_turns",
    "turn_reminder",
)


from dokimi.eval.question_text import EmptyQuestionError

def forwarded_run_config(args: argparse.Namespace, **overrides) -> dict:
    """Copy the shared run-configuration fields off *args*.

    Fields absent from *args* are omitted rather than defaulted, so
    ``_build_arms_output`` applies its own default instead of this helper
    inventing one. Pass *overrides* for values the caller sets itself.
    """
    forwarded = {
        field: getattr(args, field)
        for field in FORWARDED_RUN_CONFIG_FIELDS
        if hasattr(args, field)
    }
    forwarded.update(overrides)
    return forwarded


def judge_panel_roles(value: str) -> str:
    """argparse type for --judge-panel: an unknown role fails before any answer is paid for."""
    from dokimi.eval.panel import JUDGE_ROLES

    unknown = [r.strip() for r in value.split(",") if r.strip() and r.strip() not in JUDGE_ROLES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown role(s) {', '.join(unknown)}; available: {', '.join(JUDGE_ROLES)}")
    return value


def cmd_arms_run(args: argparse.Namespace) -> None:
    """Run an N-arm treatment comparison and optionally judge it."""
    try:
        specs = _parse_arm_specs(args.arms)
        questions = _load_questions(args.questions)
        _apply_named_matrix_cell(args)
        output = _build_arms_output(args, specs, questions)
    except EmptyQuestionError:
        raise  # a bad input record, not an arm setup error: cli.main reports it (exit 2)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error building arms/plugins: {exc}", file=sys.stderr)
        sys.exit(1)

    out_json = Path(args.out_json) if args.out_json else Path(f"results/arms/{args.product}.json")
    out_md = Path(args.out_md) if args.out_md else Path(f"results/arms/{args.product}.md")
    _save_arms_output(output, out_json, out_md)

    if output.get("summary", {}).get("per_arm"):
        print("\nSummary (avg aggregate):")
        for arm, stats in output["summary"]["per_arm"].items():
            avg = stats.get("avg_aggregate")
            delta = stats.get("delta_vs_baseline")
            avg_s = "n/a" if avg is None else f"{avg:.1f}"
            delta_s = "" if (delta is None or arm == args.baseline_arm) else f" (delta {delta:+.1f})"
            print(f"  {arm:<24} {avg_s}{delta_s}")


def cmd_arms_matrix_run(args: argparse.Namespace) -> None:
    """Run every cell in a matrix.cells descriptor and write a rollup."""
    from dokimi.artifacts import save_artifact
    from dokimi.eval.cells import load_matrix_cells
    from dokimi.eval.matrix_rollup import build_matrix_rollup, strip_internal_payloads
    from dokimi.report.matrix_rollup_report import render_matrix_rollup_report
    from dokimi.report.plugin_delta_report import render_plugin_delta_report

    try:
        specs = _parse_arm_specs(args.arms)
        questions = _load_questions(args.questions)
        cells = load_matrix_cells(args.matrix_cells)
        _validate_supported_matrix_harnesses(cells)
        _validate_unique_cell_filenames(cells)
        for cell in cells:
            cell_args = argparse.Namespace(**vars(args))
            _apply_matrix_cell(cell_args, cell)
            _check_sampling_support(cell_args)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Error loading matrix run inputs: {exc}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir) if args.out_dir else Path(f"results/arms/{args.product}-matrix")
    out_dir.mkdir(parents=True, exist_ok=True)
    cells_dir = out_dir / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)
    cell_artifacts = []
    for cell in cells:
        cell_args = argparse.Namespace(**vars(args))
        _apply_matrix_cell(cell_args, cell)
        safe_name = _safe_name(cell.name)
        out_json = cells_dir / f"{safe_name}.json"
        out_md = cells_dir / f"{safe_name}.md"
        try:
            output = _build_arms_output(cell_args, specs, questions)
        except EmptyQuestionError:
            raise  # a bad input record, not a cell error: cli.main reports it (exit 2)
        except (ValueError, FileNotFoundError) as exc:
            print(f"Error running matrix cell {cell.name!r}: {exc}", file=sys.stderr)
            sys.exit(1)
        _save_arms_output(output, out_json, out_md)
        cell_artifacts.append((cell.name, out_json, output))

    rollup = build_matrix_rollup(
        library_name=args.product,
        cell_artifacts=cell_artifacts,
        out_dir=out_dir,
        compute_plugin_deltas=args.plugin_deltas,
    )
    for row in rollup.get("plugin_deltas", []):
        payload = row.get("_artifact_payload")
        if not payload:
            continue
        delta_json = Path(row["artifact"])
        save_artifact("plugin_delta", payload, delta_json)
        delta_md = delta_json.with_suffix(".md")
        delta_md.write_text(render_plugin_delta_report(payload), encoding="utf-8")
        print(f"OK Saved plugin delta:    {delta_json}")

    persisted = strip_internal_payloads(rollup)
    out_json = Path(args.out_json) if args.out_json else out_dir / "matrix-rollup.json"
    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    save_artifact("matrix_rollup", persisted, out_json)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_matrix_rollup_report(persisted), encoding="utf-8")
    print(f"OK Saved matrix rollup:   {out_json}")
    print(f"OK Saved matrix report:   {out_md}")


def cmd_arms_plugin_delta(args: argparse.Namespace) -> None:
    """Compare paired no-plugin/plugin arms artifacts."""
    from dokimi.artifacts import load_artifact, save_artifact
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
    except (PluginDeltaError, ValueError, FileNotFoundError) as exc:
        print(f"Error computing plugin delta: {exc}", file=sys.stderr)
        sys.exit(1)

    out_json = Path(args.out_json) if args.out_json else Path("results/arms/plugin_delta.json")
    save_artifact("plugin_delta", output, out_json)
    print(f"OK Saved plugin delta: {out_json}")

    out_md = Path(args.out_md) if args.out_md else out_json.with_suffix(".md")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_plugin_delta_report(output), encoding="utf-8")
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
                            choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
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
    arms_run_p.add_argument("--top-k", type=positive_int, default=5, dest="top_k",
                            help="Docs to retrieve before reranking, for doc/MCP arms (default: 5)")
    arms_run_p.add_argument("--rerank-threshold", type=float, default=0.3, dest="rerank_threshold")
    arms_run_p.add_argument("--max-iterations", type=positive_int, default=6, dest="max_iterations",
                            help="Max tool-call rounds for agentic arms (agent:/skill-agent:, default: 6)")
    arms_run_p.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens",
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
    arms_run_p.add_argument("--out-json", default=None, dest="out_json",
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
    matrix_run_p.add_argument("--out-json", default=None, dest="out_json",
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
    plugin_delta_p.add_argument("--out-json", default=None, dest="out_json")
    plugin_delta_p.add_argument("--out-md", default=None, dest="out_md")
    plugin_delta_p.set_defaults(func=cmd_arms_plugin_delta)


def _resolve_doc_library_id(treatments, product: str, explicit_id=None, registry=None):
    """Resolve a library id for the first doc arm, before plugin wrapping.

    Same three-step order as the ``answers``/``questions`` commands: explicit
    flag, then the product's ``context7_id`` in ``products.yaml``, then the
    client's own resolution. When no id can be named the ``ValueError`` is
    raised to the caller, which stops the run: substituting the product name
    would query an id that does not exist and record an empty-retrieval doc
    arm as a measurement. Other failures (an unreachable MCP server, say) still
    fall back to the product name and are reported by the arm itself.
    """
    if explicit_id is not None:
        return explicit_id

    from dokimi.mcp.factory import resolve_library_id_for_product
    from dokimi.treatments.arms import DocTreatment

    for treatment in treatments:
        unwrapped = getattr(treatment, "inner", treatment)
        if isinstance(unwrapped, DocTreatment):
            try:
                return resolve_library_id_for_product(
                    product, unwrapped.mcp_client, registry=registry
                )
            except ValueError:
                raise
            except Exception:
                return product
    return None


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
                        choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
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
    parser.add_argument("--top-k", type=positive_int, default=5, dest="top_k",
                        help="Docs to retrieve before reranking, for doc/MCP arms (default: 5)")
    parser.add_argument("--rerank-threshold", type=float, default=0.3, dest="rerank_threshold")
    parser.add_argument("--max-iterations", type=positive_int, default=6, dest="max_iterations",
                        help="Max tool-call rounds for agentic arms (agent:/skill-agent:, default: 6)")
    parser.add_argument("--max-tokens", type=positive_int, default=None, dest="max_tokens",
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
    material belongs to which answer. Duplicated ids are refused, since there is no right
    answer to which question they mean.
    """
    from dokimi.questions.artifact import load_questions

    questions = load_questions(path).questions
    ids = [question_id(q) for q in questions]
    for i, qid in enumerate(ids):
        # The records reader refuses any other id, so a run with one would be paid for and lost.
        if qid is not None and (isinstance(qid, bool) or not isinstance(qid, (str, int))):
            raise ValueError(f"{path}: questions[{i}]: id must be a string or an integer, got {qid!r}")
    _refuse_duplicate_ids(ids, path)
    print(f"Loaded {len(questions)} questions from {path}")
    return questions


def _refuse_duplicate_ids(ids: list, path: str) -> None:
    """Raise on a repeated id."""
    counts = Counter(qid for qid in ids if qid is not None)
    dupes = sorted(qid for qid, n in counts.items() if n > 1)
    if dupes:
        raise ValueError(f"duplicate question ids in {path}: {', '.join(map(repr, dupes[:5]))}")


def _validate_expected_skills(questions: list[dict], treatments) -> None:
    """Refuse ``expected_skill`` labels the selection summary cannot score.

    Checked before any call is spent: a label of the wrong type crashes the
    summary after the run, and a name no skill arm offers silently scores as a
    miss. Names are checked only when some arm offers skills.
    """
    from dokimi.treatments import SkillAgentTreatment, SkillCatalogTreatment

    def leaves(t):
        t = getattr(t, "inner", t)
        for sub in getattr(t, "treatments", None) or [t]:
            yield sub

    known: set = set()
    for t in treatments:
        for sub in leaves(t):
            if isinstance(sub, SkillCatalogTreatment):
                known.update(s.name for s in sub.skills)
            elif isinstance(sub, SkillAgentTreatment):
                known.add(sub.skill.name)
    for i, q in enumerate(questions):
        if "expected_skill" not in q:
            continue
        label = q["expected_skill"]
        qid = question_id(q)
        qid = f"#{i}" if qid is None else qid
        names = label if isinstance(label, list) else [label] if label is not None else []
        if not all(isinstance(n, str) and n for n in names):
            raise ValueError(f"question {qid}: expected_skill must be a skill name, a list of "
                             f"names, or null; got {label!r}")
        unknown = [n for n in names if known and n not in known]
        if unknown:
            raise ValueError(f"question {qid}: expected_skill names no offered skill: "
                             f"{', '.join(unknown)}")


def _apply_named_matrix_cell(args: argparse.Namespace) -> None:
    from dokimi.eval.cells import load_matrix_cells, select_matrix_cell

    if args.matrix_cells or args.matrix_cell:
        if not args.matrix_cells or not args.matrix_cell:
            raise ValueError("--matrix-cells and --matrix-cell must be passed together")
        matrix_cell = select_matrix_cell(load_matrix_cells(args.matrix_cells), args.matrix_cell)
        _validate_supported_matrix_harnesses([matrix_cell])
        _apply_matrix_cell(args, matrix_cell)


def _apply_matrix_cell(args: argparse.Namespace, matrix_cell) -> None:
    args.matrix_cell = matrix_cell.name
    args.model = matrix_cell.model
    args.provider = matrix_cell.provider
    args.harness = matrix_cell.harness
    args.plugins = ",".join(matrix_cell.plugin_specs)


def _check_sampling_support(args: argparse.Namespace) -> None:
    """Reject sampling flags the answer provider would refuse on every answer."""
    from dokimi.llm import unsupported_sampling_params

    bad = unsupported_sampling_params(
        args.model, args.provider,
        temperature=getattr(args, "temperature", None),
        seed=getattr(args, "seed", None),
    )
    if bad:
        raise ValueError(
            f"Provider '{args.provider}' does not support {', '.join(bad)} for "
            f"model '{args.model}'. Drop the flag(s); every answer would "
            "otherwise fail and the report would show null scores."
        )
    _probe_sampling(args.model, args.provider,
                    getattr(args, "temperature", None), getattr(args, "seed", None))


@functools.lru_cache(maxsize=None)
def _probe_sampling(model: str, provider: str, temperature, seed) -> None:
    """One short real call with the requested sampling, so a value the provider
    rejects (gpt-5 accepts only temperature=1) fails before the run, not per answer."""
    if temperature is None and seed is None:
        return
    from dokimi.llm import llm_call_with_usage

    try:
        llm_call_with_usage("Reply with OK.", model=model, provider=provider,
                            max_tokens=16, temperature=temperature, seed=seed)
    except Exception as exc:
        raise ValueError(
            f"Probe call to '{provider}/{model}' with temperature={temperature}, "
            f"seed={seed} failed, so every call in the run would too: {exc}"
        ) from exc


def _refuse_judged_session(args: argparse.Namespace) -> None:
    """Refused before any answer is paid for: a session is measured, not judged."""
    if getattr(args, "session_turns", None) is not None and args.judge:
        raise ValueError("--session-turns is measurement-only; drop --judge")


def _build_arms_output(args: argparse.Namespace, specs: list[str], questions: list[dict]) -> dict:
    from dokimi.eval import Judge, JudgePanel
    from dokimi.eval.panel import JUDGE_ROLES, JudgeConfig
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.plugins import (
        create_plugins,
        plugin_set_metadata,
        validate_plugins_for_harness,
        wrap_treatments,
    )
    from dokimi.treatments import create_treatments

    _refuse_judged_session(args)
    treatments = create_treatments(specs, top_k=args.top_k, rerank_threshold=args.rerank_threshold)
    _validate_expected_skills(questions, treatments)
    try:
        library_id = _resolve_doc_library_id(treatments, args.product, args.context7_id)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    plugins = create_plugins([s.strip() for s in args.plugins.split(",") if s.strip()])
    validate_plugins_for_harness(plugins, args.harness)
    treatments = wrap_treatments(treatments, plugins)
    plugin_set = plugin_set_metadata(plugins)
    print(f"Arms: {', '.join(t.name for t in treatments)}")
    if args.matrix_cell:
        print(f"Matrix cell: {args.matrix_cell}")
    if plugin_set["plugins"]:
        print(f"Plugins: {plugin_set['plugin_set']} ({plugin_set['plugin_set_id']})")

    _check_sampling_support(args)
    if args.judge:
        from dokimi.eval.question_text import require_question_text

        # The judge refuses a blank question; here, that is before any answer is paid for.
        # Every question is answered and then judged, so every one is checked.
        for i, q in enumerate(questions):
            require_question_text(q, i)
        from dokimi.llm import unsupported_sampling_params
        from dokimi.utils import bedrock_litellm_model

        judge_model = args.judge_model
        if args.judge_provider == "amazon-bedrock" and not getattr(args, "judge_panel", None):
            # A single Bedrock judge goes through get_llm, which maps a bare name first.
            judge_model = bedrock_litellm_model(args.judge_model)
        bad_judge = unsupported_sampling_params(
            judge_model, args.judge_provider,
            temperature=getattr(args, "judge_temperature", None),
        )
        if bad_judge:
            raise ValueError(
                f"Judge provider '{args.judge_provider}' does not support "
                f"{', '.join(bad_judge)} for model '{args.judge_model}'. Drop the "
                "flag; every score would otherwise be null."
            )
        _probe_sampling(judge_model, args.judge_provider,
                        getattr(args, "judge_temperature", None), None)

    # Built before any answer is paid for: an unsupported judge provider raises here.
    judge: Judge | JudgePanel | None = None
    if args.judge:
        panel_roles = [r.strip() for r in (getattr(args, "judge_panel", "") or "").split(",") if r.strip()]
        if panel_roles:
            unknown = [r for r in panel_roles if r not in JUDGE_ROLES]
            if unknown:
                raise ValueError(
                    f"Unknown --judge-panel role(s): {', '.join(unknown)}. "
                    f"Available: {', '.join(JUDGE_ROLES)}"
                )
            # A panel reports grader spread and agreement per answer, which a
            # single judge structurally cannot — judge choice alone moves scores.
            judge = JudgePanel(
                judges=[
                    JudgeConfig(role=r, model=args.judge_model,
                                provider=args.judge_provider,
                                temperature=getattr(args, "judge_temperature", None))
                    for r in panel_roles
                ],
            )
        else:
            judge = Judge(model=args.judge_model, provider=args.judge_provider,
                          temperature=getattr(args, "judge_temperature", None))

    runner = ArmRunner(
        treatments,
        model=args.model,
        provider=args.provider,
        max_iterations=args.max_iterations,
        harness=args.harness,
        plugin_set=plugin_set,
        matrix_cell=args.matrix_cell,
        max_tokens=getattr(args, "max_tokens", None),
        grounded_from_topics=getattr(args, "grounded_from_topics", False),
        stats_correction=getattr(args, "stats_correction", "holm"),
        temperature=getattr(args, "temperature", None),
        seed=getattr(args, "seed", None),
        session_turns=getattr(args, "session_turns", None),
        turn_reminder=getattr(args, "turn_reminder", None),
    )
    records = runner.run(
        library_name=args.product,
        questions=questions,
        library_id=library_id,
        concurrency=args.concurrency,
    )

    evaluations = None
    if judge is not None:
        if isinstance(judge, JudgePanel):
            roles = [j.role for j in judge.judges]
            print(f"Judging arms with a {len(roles)}-judge panel: {', '.join(roles)}")
        else:
            print("Judging arms...")
        evaluations = runner.judge(
            judge, args.product, records,
            baseline_arm=args.baseline_arm, concurrency=args.concurrency,
        )
    return runner.build_output(
        args.product, records, evaluations=evaluations, baseline_arm=args.baseline_arm,
        judge=judge,
    )


def _save_arms_output(output: dict, out_json: Path, out_md: Path) -> None:
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.report.arms_report import render_arms_report

    ArmRunner.save(output, out_json)
    print(f"OK Saved arms comparison: {out_json}")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_arms_report(output), encoding="utf-8")
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


def _validate_supported_matrix_harnesses(cells) -> None:
    """Reject matrix harness labels that the in-process runner cannot execute."""
    from dokimi.eval.cells import HARNESS_ARMS_RUNNER

    unsupported = [
        cell.harness
        for cell in cells
        if cell.harness != HARNESS_ARMS_RUNNER
    ]
    if unsupported:
        raise ValueError(
            "matrix cells only support harness='arms-runner' until other runners are wired; "
            "unsupported harnesses: "
            + ", ".join(sorted(set(unsupported)))
        )


def _safe_name(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_", ".") else "-" for c in name)
    return safe.strip("-") or "cell"
