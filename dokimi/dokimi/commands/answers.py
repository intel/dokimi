# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""answers subcommand group: generate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dokimi.commands.evaluate import _require_ragas, _run_ragas_eval
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_CONCURRENCY,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
    DEFAULT_MAX_TOKENS_PER_QUESTION,
    DEFAULT_RETRIEVAL_TOP_K,
)


def cmd_answers_generate(args: argparse.Namespace) -> None:
    """Generate context-arm and baseline answers for questions."""
    if getattr(args, "ragas", False):
        _require_ragas()
    import yaml
    from dokimi.eval import Answerer
    from dokimi.mcp.factory import (
        create_doc_source_client,
        resolve_library_id_for_product,
    )

    # Load config
    from dokimi.config import runtime_config_path

    config_path = runtime_config_path()
    config: dict = {}
    if config_path.exists():
        try:
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            print(f"Error: invalid YAML in {config_path}: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        if loaded is not None and not isinstance(loaded, dict):
            print(f"Error: expected a mapping in {config_path}", file=sys.stderr)
            raise SystemExit(1)
        config = loaded or {}

    _product_config = config.get("products", {}).get(args.product, {})
    _retrieval_cfg = config.get("retrieval", {})
    if args.top_k is None:
        args.top_k = _retrieval_cfg.get("top_k", DEFAULT_RETRIEVAL_TOP_K)

    # Load questions
    from dokimi.questions.artifact import load_questions

    try:
        questions = load_questions(args.questions).questions
    except ValueError as exc:  # ArtifactValidationError: names the record and the fix
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"Loaded {len(questions)} questions from {args.questions}")

    # Setup documentation source client
    doc_source = getattr(args, "doc_source", "context7")
    print(f"Documentation source: {doc_source}")
    mcp_client = create_doc_source_client(doc_source)
    context7_id = getattr(args, "context7_id", None)
    if context7_id:
        print(f"Using explicit Context7 library ID: {context7_id}")
    try:
        library_id = resolve_library_id_for_product(
            args.product, mcp_client, explicit_id=context7_id
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(f"Generating answers for {args.product} (library_id={library_id})")
    print(f"Using model: {args.provider}/{args.model}")

    # Generate answers
    answerer = Answerer(
        mcp_client=mcp_client,
        model=args.model,
        provider=args.provider,
        top_k=args.top_k,
        rerank_threshold=args.rerank_threshold,
        debug_retrieval=args.debug_retrieval
    )

    if args.output:
        output_path = Path(args.output)
    elif getattr(args, "run_id", None):
        output_path = Path(f"results/{args.product.lower()}_{args.run_id}/answers/{args.product}.json")
    else:
        output_path = Path(f"answers/{args.product}.json")

    answers = answerer.generate_answers(
        library_name=args.product,
        library_id=library_id,
        questions=questions,
        max_tokens_per_question=args.max_tokens,
        output_path=output_path,
        concurrency=args.concurrency,
    )

    # generate_answers already saves incrementally to output_path after each
    # question, so the final write here is a safety flush only (ensures the
    # file reflects any last-second ordering fix).
    answerer.save_answers(answers, output_path)
    print(f"\n✅ Saved answers to {output_path}")

    # Optional RAGAS meta-evaluation (--ragas flag)
    if getattr(args, "ragas", False):
        _run_ragas_eval(answers, output_path, args)


def register(sub, positive_int) -> None:
    """Add the `answers` subcommand group."""
    answers_p = sub.add_parser("answers", help="Answer generation")
    answers_sub = answers_p.add_subparsers(dest="answers_cmd", required=True)

    # answers generate
    gen_a_p = answers_sub.add_parser("generate", help="Generate context-arm and baseline answers")
    gen_a_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    gen_a_p.add_argument("--questions", required=True, help="Path to questions JSON file")
    gen_a_p.add_argument("--run-id", default=None, dest="run_id",
                         help="Run tag for multi-model comparison (e.g. gpt4o). "
                              "Sets output to results/{product}_{run_id}/answers/{product}.json")
    gen_a_p.add_argument("--output", default=None, help="Output file (default: answers/{product}.json)")
    gen_a_p.add_argument("--model", default=DEFAULT_ANSWER_MODEL, help="LLM model for answering")
    gen_a_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
    gen_a_p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS_PER_QUESTION, help="Max tokens to retrieve per question")
    gen_a_p.add_argument("--top-k", type=int, default=None, help=f"Number of docs to retrieve before reranking (default from config/products.yaml retrieval.top_k or {DEFAULT_RETRIEVAL_TOP_K})")
    gen_a_p.add_argument("--rerank-threshold", type=float, default=0.3, help="Min relevance score (0-1) to keep docs")
    gen_a_p.add_argument("--debug-retrieval", action="store_true", help="Include retrieval metadata in output")
    gen_a_p.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY, help="Parallel API calls (default: 5)")
    gen_a_p.add_argument("--doc-source", default="context7",
                         help="Documentation source: 'context7' (default), 'local:<path>', 'url:<url>'")
    gen_a_p.add_argument("--context7-id", default=None, dest="context7_id",
                         help="Explicit Context7 library ID. Overrides auto-resolution.")
    gen_a_p.add_argument("--ragas", action="store_true",
                         help="Run RAGAS meta-evaluation after answer generation and save alongside answers.")
    gen_a_p.add_argument("--ragas-model", default=DEFAULT_JUDGE_MODEL,
                         help=f"LLM for RAGAS judge (default: {DEFAULT_JUDGE_MODEL})")
    gen_a_p.add_argument("--ragas-provider", default=DEFAULT_JUDGE_PROVIDER,
                         choices=["openai", "anthropic"],
                         help=f"Provider for RAGAS judge LLM (default: {DEFAULT_JUDGE_PROVIDER})")
    gen_a_p.set_defaults(func=cmd_answers_generate)
