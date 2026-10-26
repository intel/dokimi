# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""questions subcommand group: generate, analyze, refine, panel-review."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

from dokimi.commands._output import exit_on_unwritable
from dokimi.defaults import DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER


def _load_questions_json(path: str):
    """Parse the questions file, or exit 1 naming it when it is missing, unreadable or not JSON."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Error: cannot read questions from {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _extract_questions(questions_data, source: str) -> list:
    """Return the question list from object- or list-shaped JSON, or exit cleanly."""
    if isinstance(questions_data, dict):
        raw = questions_data.get("questions", [])
    elif isinstance(questions_data, list):
        raw = questions_data
    else:
        print(f"Error: expected a JSON object or list of questions in {source}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(raw, list):
        print(f"Error: 'questions' must be a list in {source}", file=sys.stderr)
        sys.exit(1)
    return raw


def _read_json_input(path: str):
    """The parsed JSON at ``path``, or exit 1 with a message naming the file."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError and bad UTF-8
        print(f"Error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _load_personas(path: str) -> list:
    """The ``personas`` list of a personas file, or exit 1 with a message naming the file."""
    data = _read_json_input(path)
    personas = data.get("personas") if isinstance(data, dict) else None
    if not isinstance(personas, list):
        print(f"Error: {path} must be a JSON object with a 'personas' list", file=sys.stderr)
        sys.exit(1)
    for persona in personas:
        if not isinstance(persona, dict) or not {"id", "name", "skill_level"} <= persona.keys():
            print(f"Error: each persona in {path} must be an object with 'id', 'name' and "
                  f"'skill_level', got: {persona!r}", file=sys.stderr)
            sys.exit(1)
    return personas


def cmd_questions_panel_review(args: argparse.Namespace) -> None:
    """Panel review of question quality with 3 role-differentiated LLM reviewers."""
    from dokimi.questions.panel_reviewer import QuestionPanelReviewer, DEFAULT_REVIEWERS

    reviewers = [r.strip() for r in args.reviewers.split(",") if r.strip()] if args.reviewers else DEFAULT_REVIEWERS
    unknown = [r for r in reviewers if r not in DEFAULT_REVIEWERS]
    if unknown:
        print(f"Error: unknown reviewers: {unknown}. Valid: {DEFAULT_REVIEWERS}", file=sys.stderr)
        sys.exit(1)

    questions_data = _load_questions_json(args.questions)

    # Extract question strings from various JSON shapes. Prefer explicit keys by
    # presence (not truthiness) so an existing-but-empty list isn't overridden.
    if isinstance(questions_data, dict):
        if "questions" in questions_data:
            raw = questions_data["questions"]
        elif "answers" in questions_data:
            # An answers file: its records carry the question as `question_text`.
            from dokimi.records import RecordError, read

            try:
                answers = read(questions_data, source=args.questions, section="answers")
            except RecordError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
            raw = [{"question": r.question_text} for r in answers]
        else:
            raw = list(questions_data.values())
    else:
        raw = questions_data
    if not isinstance(raw, list):
        print("Error: expected a list of questions", file=sys.stderr)
        sys.exit(1)
    questions = [
        (q.get("question") or q.get("text") or "").strip() if isinstance(q, dict) else str(q)
        for q in raw if q
    ]
    questions = [q for q in questions if q]  # drop empty strings

    output_path = Path(args.output) if args.output else Path(f"reports/{args.product}_question_panel.json")
    n_eff = min(len(questions), args.limit) if args.limit else len(questions)
    print(f"Panel review: {n_eff} questions × {len(reviewers)} reviewers → {n_eff * len(reviewers)} LLM calls")
    print(f"Reviewers: {', '.join(reviewers)} | Model: {args.provider}/{args.model}\n")

    reviewer = QuestionPanelReviewer(
        reviewers=reviewers, model=args.model,
        provider=args.provider, concurrency=args.concurrency,
    )
    with exit_on_unwritable(output_path):
        report = reviewer.review_questions(questions, library_name=args.product,
                                            output_path=output_path, limit=args.limit)

    s = report.summary
    print(f"\n{'─'*55}")
    print(f"Panel Review Summary: {args.product}")
    print(f"  Total reviewed : {report.total}")
    print(f"  ✅ Keep        : {s.get('keep', 0)}")
    print(f"  ✏️  Revise      : {s.get('revise', 0)}")
    print(f"  ❌ Drop        : {s.get('drop', 0)}")
    print(f"  Mean score     : {s.get('mean_panel_score', '—')}/100")
    if s.get("top_flags"):
        print(f"  Top issues     : {', '.join(f'{k}({v})' for k, v in s['top_flags'])}")
    print(f"\n✅ Full report: {output_path}")


def cmd_questions_refine(args: argparse.Namespace) -> None:
    """Refine a question set: normalise schema, deduplicate, filter trivial, report gaps."""
    from dokimi.questions.refiner import QuestionRefiner, GapFiller, _count_difficulty

    from dokimi.questions.artifact import load_questions

    # Refining is how a set is repaired, so it reads tolerantly without refusing; the refined
    # set is validated when it is written.
    try:
        raw = load_questions(args.questions, validate=False).questions
    except OSError as exc:
        print(f"Error: cannot read {args.questions}: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:  # ArtifactValidationError: malformed JSON structure
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    target = {
        "beginner":     args.target_beginner,
        "intermediate": args.target_intermediate,
        "advanced":     args.target_advanced,
    }
    gap_filler = None
    if getattr(args, "fill_gaps", False):
        gap_filler = GapFiller(
            library_name=args.product,
            model=args.fill_model,
            provider=args.fill_provider,
        )
        print(f"Gap fill enabled: {args.fill_provider}/{args.fill_model}")
    refiner = QuestionRefiner(
        library_name=args.product,
        target_distribution=target,
        sim_threshold=args.sim_threshold,
        gap_filler=gap_filler,
    )
    def _validate_before_fill(cleaned: list) -> None:
        # The set the gap filler extends must already be valid, so a set that would be refused
        # at save time fails here, before any paid model call.
        from dokimi.artifacts import validate_artifact
        from dokimi.questions.artifact import upgrade_questions

        validate_artifact("questions", upgrade_questions({
            "library": args.product,
            "total": len(cleaned),
            "difficulty_distribution": _count_difficulty(cleaned),
            "questions": cleaned,
        }))

    try:
        report = refiner.refine(raw, before_fill=_validate_before_fill if gap_filler else None)
    except ValueError as exc:  # ArtifactValidationError: the input set cannot be gap-filled
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(report.summary())

    if not args.dry_run:
        from dokimi.questions.artifact import save_questions_artifact

        out = Path(args.output) if args.output else Path(f"questions/{args.product}_refined.json")
        try:
            with exit_on_unwritable(out):
                save_questions_artifact({
                    "library": args.product,
                    "total": len(report.questions),
                    "difficulty_distribution": report.difficulty_after,
                    "questions": report.questions,
                }, out)
        except ValueError as exc:  # ArtifactValidationError: the refined set is not valid v2
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"\n✅ Refined questions saved to {out}")
    else:
        print("\n(dry-run: no file written)")


def cmd_questions_analyze(args: argparse.Namespace) -> None:
    """Analyze question quality: difficulty distribution and triviality detection."""
    from dokimi.questions.quality_analyzer import QuestionQualityAnalyzer
    from dokimi.questions.normalizer import normalize_questions

    questions_data = _load_questions_json(args.questions)
    raw = _extract_questions(questions_data, args.questions)
    if raw and isinstance(raw[0], dict):
        normalized = normalize_questions(raw)
        questions = [q["question"] for q in normalized]
    else:
        questions = [str(q) for q in raw]

    print(f"Analyzing {len(questions)} questions for '{args.product}'...")
    print(f"Model: {args.provider}/{args.model} | concurrency: {args.concurrency}\n")

    analyzer = QuestionQualityAnalyzer(
        model=args.model,
        provider=args.provider,
        concurrency=args.concurrency,
    )
    report = analyzer.analyze(questions, library_name=args.product)

    # Print summary
    dist = report.difficulty_distribution
    print(f"─── Quality Report: {args.product} ───")
    print(f"Total questions : {report.total}")
    print(f"Difficulty      : beginner={dist.get('beginner',0)}  intermediate={dist.get('intermediate',0)}  advanced={dist.get('advanced',0)}")
    print(f"Trivial         : {report.trivial_count} ({report.trivial_pct}%)")
    print(f"Diversity score : {report.diversity_score:.3f}  (1.0 = perfectly balanced)")
    print()
    print("Recommendations:")
    for rec in report.recommendations:
        print(f"  • {rec}")

    if report.trivial_questions:
        print(f"\nTrivial questions ({len(report.trivial_questions)}):")
        for q in report.trivial_questions[:5]:
            print(f"  - {q}")
        if len(report.trivial_questions) > 5:
            print(f"  … and {len(report.trivial_questions) - 5} more (see report file)")

    output_path = Path(args.output) if args.output else Path(f"reports/{args.product}_question_quality.json")
    with exit_on_unwritable(output_path):
        analyzer.save_report(report, output_path)
    print(f"\n✅ Full report saved to {output_path}")


def print_validation_stats(stats: Dict[str, Any]) -> None:
    """Print what `validate_and_dedupe` actually did, including the no-ops."""
    print("✓ Validation complete:")
    print(f"  Initial: {stats['initial_count']}")
    print(f"  After validation: {stats['after_validation']}")
    print(f"  After deduplication: {stats['after_deduplication']}")
    print(f"  Removed (low score): {stats['removed_low_score']}")
    print(f"  Removed (duplicates): {stats['removed_duplicates']}")
    if stats["embedding_provider"] == "none":
        print("  Embeddings: none")
    else:
        print(f"  Embeddings: {stats['embedding_provider']}"
              f":{stats['embedding_model']} @ {stats['similarity_threshold']}")
    if not stats["after_prefilter"]:
        # Nothing reached the scorer, so nothing "passed through" either.
        print("  ⚠ No questions left to validate (none generated, or all "
              "removed by the synthetic-framing pre-filter)")
    elif not stats["validation_performed"]:
        print("  ⚠ NO questions were scored — all passed through unvalidated")
    elif stats["unscored_questions"]:
        print(f"  ⚠ {stats['unscored_questions']} questions passed through "
              f"unscored (validation call failed)")
    if not stats["deduplication_performed"]:
        reason = f" ({stats['deduplication_error']})" if stats.get("deduplication_error") else ""
        print(f"  ⚠ Deduplication did NOT run{reason} — duplicates were kept")


def cmd_questions_generate(args: argparse.Namespace) -> None:
    """Generate questions from personas and seed topics."""
    from dokimi.commands.answers import _load_runtime_config
    from dokimi.questions import RagasSeedExtractor, QuestionGenerator, QuestionValidator

    config = _load_runtime_config()

    _product_config = (config.get("products") or {}).get(args.product, {})
    _retrieval_cfg = config.get("retrieval") or {}
    if args.top_k is None:
        args.top_k = _retrieval_cfg.get("top_k", 3)

    # Load personas
    personas = _load_personas(args.personas)
    print(f"Loaded {len(personas)} personas from {args.personas}")

    # Get or extract topics
    if args.topics:
        topics = _read_json_input(args.topics)
        if not isinstance(topics, list):
            print(f"Error: {args.topics} must be a JSON list of topics", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Extracting seed topics for {args.product}...")

        # Setup documentation source client
        doc_source = getattr(args, "doc_source", "context7")
        from dokimi.mcp.factory import (
            create_doc_source_client,
            resolve_library_id_for_product,
        )
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

        extractor = RagasSeedExtractor(mcp_client=mcp_client, cache_dir=Path(".cache/topics"))
        topics = extractor.extract_topics(
            library_id=library_id,
            library_name=args.product,
            max_topics=20
        )
        print(f"✓ Extracted {len(topics)} seed topics")

    # Generate questions
    print(f"Generating questions using {args.provider}/{args.model}...")
    generator = QuestionGenerator(model=args.model, provider=args.provider)

    questions = generator.generate_questions(
        library_name=args.product,
        personas=personas,
        topics=topics,
        questions_per_topic=args.count
    )

    print(f"✓ Generated {len(questions)} questions")

    # Validate and dedupe (optional)
    if args.validate:
        print("Validating and deduplicating...")
        validator = QuestionValidator(
            llm_model=args.model,
            llm_provider=args.provider,
            threshold=60,
            embedding_provider=args.embeddings,
        )

        questions, stats = validator.validate_and_dedupe(args.product, questions)

        print_validation_stats(stats)

    # Save output
    output_path = Path(args.output) if args.output else Path(f"questions/{args.product}.json")
    try:
        with exit_on_unwritable(output_path):
            generator.save_questions(questions, output_path)
    except ValueError as exc:  # ArtifactValidationError: the generated set is not valid v2
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\n✅ Saved {len(questions)} questions to {output_path}")


def register(sub, positive_int) -> None:
    """Add the `questions` subcommand group."""
    questions_p = sub.add_parser("questions", help="Question generation")
    questions_sub = questions_p.add_subparsers(dest="questions_cmd", required=True)

    # questions generate
    gen_q_p = questions_sub.add_parser("generate", help="Generate questions from personas and topics")
    gen_q_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    gen_q_p.add_argument("--personas", required=True, help="Path to personas JSON file")
    gen_q_p.add_argument("--output", default=None, help="Output file (default: questions/{product}.json)")
    gen_q_p.add_argument("--topics", default=None, help="Optional: path to topics JSON (auto-extracted if not provided)")
    gen_q_p.add_argument("--top-k", type=int, default=None, dest="top_k", help="Docs to retrieve per topic (default from config)")
    gen_q_p.add_argument("--count", type=int, default=2, help="Questions per topic per persona")
    gen_q_p.add_argument("--validate", action="store_true", help="Enable validation and deduplication")
    gen_q_p.add_argument("--embeddings", default="auto", choices=["auto", "openai", "local"],
                         help="Embedding backend for deduplication: openai, local "
                              "(sentence-transformers, no API key), or auto "
                              "(openai when a key is set, else local)")
    gen_q_p.add_argument("--model", default=DEFAULT_GENERATOR_MODEL, help="LLM model for generation")
    gen_q_p.add_argument("--provider", default=DEFAULT_GENERATOR_PROVIDER, choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
    gen_q_p.add_argument("--doc-source", default="context7",
                         help="Documentation source for topic extraction: 'context7', 'local:<path>', 'url:<url>'")
    gen_q_p.add_argument("--context7-id", default=None, dest="context7_id",
                         help="Explicit Context7 library ID. Overrides auto-resolution.")
    gen_q_p.set_defaults(func=cmd_questions_generate)

    # questions analyze
    analyze_q_p = questions_sub.add_parser("analyze", help="Analyze question quality (difficulty + triviality)")
    analyze_q_p.add_argument("--questions", required=True, help="Path to questions JSON file")
    analyze_q_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    analyze_q_p.add_argument("--output", default=None, help="Output report JSON (default: reports/{product}_question_quality.json)")
    analyze_q_p.add_argument("--model", default=DEFAULT_GENERATOR_MODEL, help="LLM model for classification")
    analyze_q_p.add_argument("--provider", default=DEFAULT_GENERATOR_PROVIDER, choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
    analyze_q_p.add_argument("--concurrency", type=int, default=5, help="Parallel classification requests")
    analyze_q_p.set_defaults(func=cmd_questions_analyze)

    # questions refine
    refine_q_p = questions_sub.add_parser("refine", help="Refine question set: normalise, deduplicate, filter trivial, report gaps")
    refine_q_p.add_argument("--questions", required=True, help="Path to questions JSON file")
    refine_q_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    refine_q_p.add_argument("--output", default=None, help="Output refined JSON (default: questions/{product}_refined.json)")
    refine_q_p.add_argument("--target-beginner", type=int, default=10, dest="target_beginner")
    refine_q_p.add_argument("--target-intermediate", type=int, default=10, dest="target_intermediate")
    refine_q_p.add_argument("--target-advanced", type=int, default=10, dest="target_advanced")
    refine_q_p.add_argument("--sim-threshold", type=float, default=0.82, dest="sim_threshold",
                             help="Edit-distance similarity threshold for deduplication (default: 0.82, range 0-1)")
    refine_q_p.add_argument("--dry-run", action="store_true", dest="dry_run",
                             help="Print report only, do not write output file")
    refine_q_p.add_argument("--fill-gaps", action="store_true", dest="fill_gaps",
                             help="Use LLM to generate questions for under-represented difficulty levels")
    refine_q_p.add_argument("--fill-model", default=DEFAULT_GENERATOR_MODEL, dest="fill_model")
    refine_q_p.add_argument("--fill-provider", default=DEFAULT_GENERATOR_PROVIDER, dest="fill_provider",
                             choices=["openai", "anthropic", "google"])
    refine_q_p.set_defaults(func=cmd_questions_refine)

    # questions panel-review
    panel_q_p = questions_sub.add_parser("panel-review",
                                          help="Multi-agent panel review of question quality")
    panel_q_p.add_argument("--questions", required=True, help="Path to questions JSON file")
    panel_q_p.add_argument("--product", required=True, help="Product name (e.g., oneDAL)")
    panel_q_p.add_argument("--output", default=None,
                            help="Output report JSON (default: reports/{product}_question_panel.json)")
    panel_q_p.add_argument("--model", default=DEFAULT_GENERATOR_MODEL)
    panel_q_p.add_argument("--provider", default=DEFAULT_GENERATOR_PROVIDER, choices=["openai", "anthropic", "amazon-bedrock", "google-vertex", "openrouter", "openai-codex"])
    panel_q_p.add_argument("--reviewers", default=None,
                            help="Comma-separated reviewers (default: domain_expert,user_advocate,qa_engineer)")
    panel_q_p.add_argument("--concurrency", type=positive_int, default=6)
    panel_q_p.add_argument("--limit", type=positive_int, default=None,
                            help="Review only first N questions")
    panel_q_p.set_defaults(func=cmd_questions_panel_review)
