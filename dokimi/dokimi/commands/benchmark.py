# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""benchmark subcommand group: run, batch."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dokimi.commands.evaluate import _warn_judge_independence
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.defaults import (
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_CONCURRENCY,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
    DEFAULT_MAX_TOKENS_PER_QUESTION,
    DEFAULT_RETRIEVAL_TOP_K,
    DEFAULT_RESULTS_SUFFIX,
)
from dokimi.commands.library import _load_registry


def _run_single_library(entry, output_dir: str, model: str, provider: str, judge_model: str, judge_provider: str = DEFAULT_JUDGE_PROVIDER, doc_source_override=None, max_tokens_per_question: int = DEFAULT_MAX_TOKENS_PER_QUESTION, force_regen: bool = False, concurrency: int = DEFAULT_CONCURRENCY, questions_from=None, questions_root=None) -> dict:
    """Run full evaluation pipeline for one ProductEntry. Returns result dict."""
    from dokimi.orchestrator import EvaluationPipeline
    from pathlib import Path as _Path

    doc_source = doc_source_override or (entry.doc_sources[0] if entry.doc_sources else "context7")
    out = _Path(output_dir)

    print(f"\n{'='*60}")
    print(f"  Library : {entry.name} ({entry.key})")
    print(f"  Source  : {doc_source}")
    print(f"  Output  : {out}")
    print(f"{'='*60}")

    _warn_judge_independence(
        answer_provider=provider,
        answer_model=model,
        judge_provider=judge_provider,
        judge_model=judge_model,
        context=f"benchmark:{entry.key}",
    )

    pipeline = EvaluationPipeline(
        product=entry.name,
        repo=entry.repo,
        description=entry.description,
        output_dir=out,
        model=model,
        provider=provider,
        judge_model=judge_model,
        judge_provider=judge_provider,
        context7_id=entry.context7_id,
        doc_source=doc_source,
        max_tokens_per_question=max_tokens_per_question,
        force_regen=force_regen,
        questions_from=questions_from,
        product_key=entry.key,
        questions_root=_Path(questions_root) if questions_root else None,
    )
    result = pipeline.run(concurrency=concurrency)
    return {"library": entry.key, "name": entry.name, "status": "ok", "result": result}


def _credential_env_var(provider: str) -> str:
    return {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "amazon-bedrock": "AWS_BEARER_TOKEN_BEDROCK",
        "google": "GEMINI_API_KEY",
        "google-vertex": "GEMINI_API_KEY",
        "openai-codex": "OPENAI_API_KEY",
    }.get(provider, "OPENAI_API_KEY")


def _resolved_benchmark_plan(entry, args: argparse.Namespace) -> dict:
    doc_source = getattr(args, "doc_source", None) or (entry.doc_sources[0] if entry.doc_sources else "context7")
    output_dir = getattr(args, "output_dir", None) or f"results/{entry.key}{DEFAULT_RESULTS_SUFFIX}"
    return {
        "target": entry.key,
        "name": entry.name,
        "description": entry.description,
        "repo": entry.repo,
        "doc_source": doc_source,
        "output_dir": output_dir,
        "answer": f"{args.provider}/{args.model}",
        "judge": f"{args.judge_provider}/{args.judge_model}",
        "questions_from": getattr(args, "questions_from", None),
        "questions_root": getattr(args, "questions_root", None),
        "retrieval": f"semantic-only, top_k={DEFAULT_RETRIEVAL_TOP_K}",
        "arms": "without_docs vs with_docs",
        "max_tokens_per_question": getattr(args, "max_tokens", DEFAULT_MAX_TOKENS_PER_QUESTION),
        "concurrency": getattr(args, "concurrency", DEFAULT_CONCURRENCY),
    }


def _print_plan(plan: dict) -> None:
    print("Resolved benchmark plan")
    for key, value in plan.items():
        if value is not None:
            print(f"  {key}: {value}")


def cmd_benchmark_preflight(args: argparse.Namespace) -> None:
    """Validate the resolved benchmark plan without running LLM calls."""
    from dokimi.orchestrator.pipeline import load_questions_payload, resolve_questions_from_path
    from dokimi.mcp.factory import create_doc_source_client

    registry = _load_registry(args)
    try:
        entry = registry.get(args.library)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    plan = _resolved_benchmark_plan(entry, args)
    _print_plan(plan)

    errors: list[str] = []
    warnings: list[str] = []

    for provider in {args.provider, args.judge_provider}:
        key = _credential_env_var(provider)
        if not os.environ.get(key):
            errors.append(f"missing {key} for provider {provider}")

    try:
        Path(plan["output_dir"]).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        errors.append(f"output dir not writable: {plan['output_dir']} ({exc})")

    try:
        client = create_doc_source_client(plan["doc_source"])
        if plan["doc_source"] == "context7" and getattr(entry, "context7_id", None):
            print(f"  context7_id: {entry.context7_id}")
        elif plan["doc_source"] == "context7":
            warnings.append("context7 id not pinned; client will resolve by target name")
        _ = client
    except Exception as exc:  # pragma: no cover - defensive for plugin clients
        errors.append(f"doc source invalid: {plan['doc_source']} ({exc})")

    if plan["questions_from"]:
        qf = resolve_questions_from_path(Path(plan["questions_from"]), entry.key)
        if not qf.exists():
            errors.append(f"questions source not found: {qf}")
        else:
            try:
                questions = load_questions_payload(qf)
                print(f"  questions: {qf} ({len(questions)} items)")
            except Exception as exc:
                errors.append(f"questions source invalid: {qf} ({exc})")

    same = _warn_judge_independence(
        answer_provider=args.provider,
        answer_model=args.model,
        judge_provider=args.judge_provider,
        judge_model=args.judge_model,
        context=f"preflight:{entry.key}",
    )
    if same:
        warnings.append("judge and answer model are identical")

    if warnings:
        print("Warnings:")
        for item in warnings:
            print(f"  ⚠ {item}")
    if errors:
        print("Errors:", file=sys.stderr)
        for item in errors:
            print(f"  ✗ {item}", file=sys.stderr)
        sys.exit(1)
    print("✅ Preflight OK")


def cmd_benchmark_run(args: argparse.Namespace) -> None:
    """Run full evaluation pipeline for a single registered library."""
    import statistics as _stats
    registry = _load_registry(args)
    try:
        entry = registry.get(args.library)
    except KeyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output_dir or f"results/{entry.key}{DEFAULT_RESULTS_SUFFIX}"
    n_runs = getattr(args, "multi_run", 1)
    concurrency = getattr(args, "concurrency", DEFAULT_CONCURRENCY)
    questions_from = getattr(args, "questions_from", None)
    questions_root = getattr(args, "questions_root", None)

    if n_runs == 1:
        _run_single_library(
            entry,
            output_dir=output_dir,
            model=args.model,
            provider=args.provider,
            judge_model=args.judge_model,
            judge_provider=getattr(args, "judge_provider", DEFAULT_JUDGE_PROVIDER),
            doc_source_override=getattr(args, "doc_source", None),
            max_tokens_per_question=getattr(args, "max_tokens", DEFAULT_MAX_TOKENS_PER_QUESTION),
            force_regen=getattr(args, "force_regen", False),
            concurrency=concurrency,
            questions_from=questions_from,
            questions_root=questions_root,
        )
    else:
        print(f"\n🔁 Multi-run mode: {n_runs} evaluation passes")
        run_averages = []
        first_run_dir = f"{output_dir}_run1"
        for i in range(1, n_runs + 1):
            run_dir = f"{output_dir}_run{i}" if n_runs > 1 else output_dir
            qfrom = questions_from if questions_from else (first_run_dir if i > 1 else None)
            print(f"\n  ── Run {i}/{n_runs} → {run_dir}")
            r = _run_single_library(
                entry,
                output_dir=run_dir,
                model=args.model,
                provider=args.provider,
                judge_model=args.judge_model,
                judge_provider=getattr(args, "judge_provider", DEFAULT_JUDGE_PROVIDER),
                doc_source_override=getattr(args, "doc_source", None),
                max_tokens_per_question=getattr(args, "max_tokens", DEFAULT_MAX_TOKENS_PER_QUESTION),
                force_regen=(getattr(args, "force_regen", False) and i == 1),
                concurrency=concurrency,
                questions_from=qfrom,
                questions_root=questions_root,
            )
            try:
                eval_summary = r["result"]["steps"]["evaluation"]["summary"]
                run_averages.append(eval_summary["with_avg"])
            except (KeyError, TypeError):
                pass
        if run_averages:
            import statistics as _stats
            std = _stats.stdev(run_averages) if len(run_averages) > 1 else 0.0
            mean = _stats.mean(run_averages)
            print(f"\n📊 Multi-run summary ({n_runs} runs): context-arm avg {mean:.1f} ± {std:.2f}")
            if std > 5.0:
                print("   ⚠️  High variance — scores are unstable (std > 5)")

    print(f"\n✅ Done: {entry.name}")


def cmd_benchmark_batch(args: argparse.Namespace) -> None:
    """Run evaluation pipeline for multiple registered libraries."""
    registry = _load_registry(args)

    if args.all_libraries or not args.libraries:
        entries = registry.list()
    else:
        keys = [k.strip() for k in args.libraries.split(",") if k.strip()]
        entries = []
        for k in keys:
            try:
                entries.append(registry.get(k))
            except KeyError as exc:
                print(f"Warning: {exc}", file=sys.stderr)

    if not entries:
        print("No libraries to run.", file=sys.stderr)
        sys.exit(1)

    print(f"Batch run: {len(entries)} libraries → {args.output_dir}")
    results = []
    failed = []

    for entry in entries:
        output_dir = str(Path(args.output_dir) / f"{entry.key}{DEFAULT_RESULTS_SUFFIX}")
        try:
            r = _run_single_library(
                entry,
                output_dir=output_dir,
                model=args.model,
                provider=args.provider,
                judge_model=args.judge_model,
                judge_provider=getattr(args, "judge_provider", DEFAULT_JUDGE_PROVIDER),
                max_tokens_per_question=getattr(args, "max_tokens", DEFAULT_MAX_TOKENS_PER_QUESTION),
                force_regen=getattr(args, "force_regen", False),
                concurrency=getattr(args, "concurrency", DEFAULT_CONCURRENCY),
                questions_root=getattr(args, "questions_root", None),
            )
            results.append(r)
        except Exception as exc:
            msg = f"FAILED: {entry.key} — {exc}"
            print(f"\n❌ {msg}", file=sys.stderr)
            failed.append({"library": entry.key, "name": entry.name, "status": "failed", "error": str(exc)})
            if args.fail_fast:
                print("Stopping (--fail-fast).", file=sys.stderr)
                break

    # Summary
    print(f"\n{'─'*50}")
    print(f"Batch complete: {len(results)} succeeded, {len(failed)} failed")
    for r in results:
        print(f"  ✅ {r['name']}")
    for f in failed:
        print(f"  ❌ {f['name']}: {f['error']}")

    if failed:
        sys.exit(1)


def register(sub, positive_int) -> None:
    """Add the `benchmark` subcommand group."""
    benchmark_p = sub.add_parser("benchmark", help="Run context benchmark for one or more registered targets")
    benchmark_sub = benchmark_p.add_subparsers(dest="benchmark_cmd", required=True)

    # benchmark run — single library from registry
    bench_run_p = benchmark_sub.add_parser("run", help="Run full benchmark for a registered target")
    bench_run_p.add_argument("--library", "--target", required=True, dest="library", help="Target key from registry (e.g., onetbb). --library kept as compatibility alias")
    bench_run_p.add_argument("--doc-source", default=None, dest="doc_source",
                             help="Override doc/context source (default: first in registry entry)")
    bench_run_p.add_argument("--output-dir", default=None, dest="output_dir",
                             help="Output directory (default: results/{target}_final)")
    bench_run_p.add_argument("--model", default=DEFAULT_ANSWER_MODEL)
    bench_run_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
    bench_run_p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, dest="judge_model")
    bench_run_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, dest="judge_provider",
                             choices=JUDGE_PROVIDERS)
    bench_run_p.add_argument("--registry", default=None, help="Path to custom products.yaml")
    bench_run_p.add_argument("--max-tokens", type=positive_int, default=DEFAULT_MAX_TOKENS_PER_QUESTION, dest="max_tokens",
                             help="Max tokens to retrieve per question from doc source (default: 4000)")
    bench_run_p.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY, dest="concurrency",
                             help="Parallel API calls for answering and judging (default: 5)")
    bench_run_p.add_argument("--force-regen", action="store_true", dest="force_regen",
                             help="Regenerate personas/questions even if cached files exist")
    bench_run_p.add_argument("--questions-from", default=None, dest="questions_from",
                             help="Reuse questions from another run's output directory or JSON file. "
                                  "Skips question generation entirely — essential for fair multi-model "
                                  "comparisons (all models evaluated on the same question set). "
                                  "Example: --questions-from results/onedal_gpt51")
    bench_run_p.add_argument("--questions-root", default=None, dest="questions_root",
                             help="Directory where the golden question set "
                                  "<target>_golden.json is looked up, for example a "
                                  "checkout of the evaluation-content repository: "
                                  "--questions-root /path/to/content-repo/questions. "
                                  "Without it the run has no golden question set.")
    bench_run_p.add_argument("--multi-run", type=positive_int, default=1, dest="multi_run",
                             metavar="N",
                             help="Run answer generation + evaluation N times (default: 1). "
                                  "N>=3 enables variance check in the trust gate. "
                                  "Results are averaged; variance is measured for stability.")
    bench_run_p.set_defaults(func=cmd_benchmark_run)

    # benchmark preflight — validate plan, no LLM calls
    bench_pre_p = benchmark_sub.add_parser("preflight", help="Validate resolved benchmark plan without running LLM calls")
    bench_pre_p.add_argument("--library", "--target", required=True, dest="library", help="Target key from registry (e.g., onetbb)")
    bench_pre_p.add_argument("--doc-source", default=None, dest="doc_source", help="Override doc/context source")
    bench_pre_p.add_argument("--output-dir", default=None, dest="output_dir", help="Output directory (default: results/{target}_final)")
    bench_pre_p.add_argument("--model", default=DEFAULT_ANSWER_MODEL)
    bench_pre_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
    bench_pre_p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, dest="judge_model")
    bench_pre_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, dest="judge_provider", choices=JUDGE_PROVIDERS)
    bench_pre_p.add_argument("--registry", default=None, help="Path to custom products.yaml")
    bench_pre_p.add_argument("--max-tokens", type=positive_int, default=DEFAULT_MAX_TOKENS_PER_QUESTION, dest="max_tokens")
    bench_pre_p.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY, dest="concurrency")
    bench_pre_p.add_argument("--questions-from", default=None, dest="questions_from", help="Question-set source for fair comparison")
    bench_pre_p.add_argument("--questions-root", default=None, dest="questions_root",
                             help="Directory where <target>_golden.json is looked up "
                                  "(e.g. /path/to/content-repo/questions)")
    bench_pre_p.set_defaults(func=cmd_benchmark_preflight)

    # benchmark batch — multiple libraries
    bench_batch_p = benchmark_sub.add_parser("batch", help="Run pipeline for multiple libraries")
    bench_batch_p.add_argument("--libraries", default=None,
                               help="Comma-separated library keys (e.g., onetbb,onemkl). "
                                    "Omit or use --all for all registered libraries.")
    bench_batch_p.add_argument("--all", action="store_true", dest="all_libraries",
                               help="Run for all libraries in registry")
    bench_batch_p.add_argument("--output-dir", default="results", dest="output_dir")
    bench_batch_p.add_argument("--model", default=DEFAULT_ANSWER_MODEL)
    bench_batch_p.add_argument("--provider", default=DEFAULT_ANSWER_PROVIDER, choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
    bench_batch_p.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, dest="judge_model")
    bench_batch_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, dest="judge_provider",
                               choices=JUDGE_PROVIDERS)
    bench_batch_p.add_argument("--max-tokens", type=positive_int, default=DEFAULT_MAX_TOKENS_PER_QUESTION, dest="max_tokens",
                               help="Max tokens to retrieve per question from doc source (default: 4000)")
    bench_batch_p.add_argument("--force-regen", action="store_true", dest="force_regen",
                               help="Regenerate personas/questions even if cached files exist")
    bench_batch_p.add_argument("--registry", default=None, help="Path to custom products.yaml")
    bench_batch_p.add_argument("--questions-root", default=None, dest="questions_root",
                               help="Directory where each <target>_golden.json is looked "
                                    "up (e.g. /path/to/content-repo/questions)")
    bench_batch_p.add_argument("--fail-fast", action="store_true", dest="fail_fast",
                               help="Stop on first failure (default: continue all)")
    bench_batch_p.set_defaults(func=cmd_benchmark_batch)
