# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""evaluate subcommand: the one-command full pipeline (orchestrator)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dokimi.llm import ANSWER_PROVIDERS
from dokimi.llm_cli import SamplingError, check_sampling
from dokimi.commands.evaluate import _warn_judge_independence
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_MODEL_FOR_PROVIDER,
    JUDGE_MODEL_HELP,
)


def cmd_evaluate(args: argparse.Namespace) -> None:
    """Run full evaluation pipeline (orchestrator)."""
    from dokimi.orchestrator import EvaluationPipeline

    if not getattr(args, "repo", None) and not getattr(args, "description", None):
        print("Error: either --repo or --description must be provided.", file=sys.stderr)
        sys.exit(1)
    try:  # before any persona, question or answer is paid for; the judge would refuse later
        check_sampling(args.judge_provider, getattr(args, "judge_temperature", None), None)
    except SamplingError as exc:  # the same exit as the other judge commands' sampling refusal
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"Starting full evaluation pipeline for {args.product}")
    if args.repo:
        print(f"Repository: {args.repo}")
    else:
        print(f"Description: {(args.description or '')[:80]}")
    print(f"Output directory: {args.output_dir}")
    if args.custom_questions:
        print(f"Custom questions: {args.custom_questions}")
    print()

    _warn_judge_independence(
        answer_provider=args.provider,
        answer_model=args.model,
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
        context="evaluate",
    )

    # Create pipeline; it checks --custom-questions and creates the output directories
    try:
        pipeline = EvaluationPipeline(
            product=args.product,
            repo=getattr(args, "repo", None),
            description=getattr(args, "description", None),
            output_dir=Path(args.output_dir),
            custom_questions_path=Path(args.custom_questions) if args.custom_questions else None,
            questions_root=Path(args.questions_root) if getattr(args, "questions_root", None) else None,
            model=args.model,
            provider=args.provider,
            judge_model=args.judge_model,
            judge_provider=args.judge_provider,
            judge_temperature=args.judge_temperature,
            personas_count=args.personas_count,
            questions_per_topic=args.questions_per_topic,
            # Only pass top_k when explicitly set; otherwise let pipeline use config default
            **({"top_k": args.top_k} if args.top_k is not None else {}),
            rerank_threshold=args.rerank_threshold,
            debug_retrieval=args.debug_retrieval,
            doc_source=getattr(args, "doc_source", "context7"),
            context7_id=getattr(args, "context7_id", None),
        )
    except OSError as exc:  # a missing --custom-questions file, an unusable --output-dir
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Run pipeline
    try:
        results = pipeline.run()

        print("\n" + "="*80)
        print("✅ Pipeline completed successfully!")
        print("="*80)
        print("\nOutput files:")
        if not results['steps']['personas'].get('skipped', False):
            print(f"  Personas:   {results['steps']['personas']['path']}")
        print(f"  Questions:  {results['steps']['questions_merged']['path']}")
        print(f"  Answers:    {results['steps']['answers']['path']}")
        print(f"  Evaluation: {results['steps']['evaluation']['path']}")
        print(f"  Report:     {results['steps']['report']['path']}")

        if "summary" in results["steps"]["evaluation"]:
            summary = results["steps"]["evaluation"]["summary"]
            print("\nResults:")
            print(f"  Context avg:      {summary['with_avg']}")
            print(f"  Baseline avg:     {summary['without_avg']}")
            print(f"  Delta:            {summary['delta_avg']:+.1f}")

        print(f"\n📊 View full report: cat {results['steps']['report']['path']}")

    except Exception as e:
        print(f"\n❌ Pipeline failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


def register(sub, positive_int) -> None:
    """Add the `evaluate` subparser (one-command pipeline)."""
    eval_p = sub.add_parser("evaluate", help="Run full evaluation pipeline (one command)")
    eval_p.add_argument("--product", required=True, help="Product name (e.g., oneDNN)")
    eval_p.add_argument("--repo", default=None, help="GitHub repo (e.g., oneapi-src/oneDNN). Optional if --description is given.")
    eval_p.add_argument("--description", default=None, help="Plain-text product description (used when --repo is not available).")
    eval_p.add_argument("--output-dir", default=".", help="Base output directory")
    eval_p.add_argument("--custom-questions", default=None, help="Optional: path to manual questions JSON")
    eval_p.add_argument("--questions-root", default=None, dest="questions_root",
                        help="Directory where the golden question set <product>_golden.json "
                             "is looked up, for example a checkout of the evaluation-content "
                             "repository: --questions-root /path/to/content-repo/questions")
    eval_p.add_argument("--model", default=DEFAULT_ANSWER_MODEL, help="LLM model for generation/answering")
    eval_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=ANSWER_PROVIDERS)
    eval_p.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    eval_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    eval_p.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                        help="Judge sampling temperature, recorded as judge_temperature in the eval output")
    eval_p.add_argument("--personas-count", type=positive_int, default=5, help="Target number of personas")
    eval_p.add_argument("--questions-per-topic", type=positive_int, default=2, help="Questions per topic per persona")
    eval_p.add_argument("--top-k", type=positive_int, default=None, help="Docs to retrieve before reranking (default from config/products.yaml retrieval.top_k)")
    eval_p.add_argument("--rerank-threshold", type=float, default=0.3, help="Min relevance score")
    eval_p.add_argument("--debug-retrieval", action="store_true", help="Include retrieval metadata")
    eval_p.add_argument("--doc-source", default="context7",
                        help="Documentation source: 'context7' (default), 'local:<path>', 'url:<url>'")
    eval_p.add_argument("--context7-id", default=None, dest="context7_id",
                        help="Explicit Context7 library ID (e.g., 'intel/mkl-dnn'). Overrides auto-resolution.")
    eval_p.set_defaults(func=cmd_evaluate)
