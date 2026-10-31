# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval subcommand group: score, ragas, panel-score."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from dokimi.llm import ANSWER_PROVIDERS
from dokimi.defaults import (
    DEFAULT_CONCURRENCY,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_MODEL_FOR_PROVIDER,
    JUDGE_MODEL_HELP,
    answers_method_version,
)
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.eval.judge_validity import DEFAULT_MIN_ACCURACY, NoUsablePairs
from dokimi.utils import normalize_model_ref, write_text_or_exit
from dokimi.exit_codes import EXIT_USAGE


def _warn_judge_independence(
    answer_provider: str,
    answer_model: str,
    judge_provider: str,
    judge_model: str,
    context: str = "run",
) -> bool:
    """Emit a non-fatal warning when answer and judge use the same model/provider."""
    from dokimi.llm_cli import same_model

    same = (normalize_model_ref(answer_provider, answer_model) == normalize_model_ref(judge_provider, judge_model)
            or same_model(answer_provider, answer_model, judge_provider, judge_model))
    if same:
        print(
            "⚠ Evaluator independence warning "
            f"({context}): answer model equals judge model "
            f"[{answer_provider}/{answer_model}] — potential self-referential bias.",
            file=sys.stderr,
        )
    return same


def _load_answers(path: str):
    """The answers artifact at ``path`` (read by dokimi/records.py), or a clean exit."""
    from dokimi.records import RecordError, load

    try:
        return load(path, section="answers")
    except RecordError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


def _write_json_or_exit(path: Path, payload: dict, code: int = EXIT_USAGE) -> None:
    """Write ``payload`` as JSON to ``path``, or exit ``code`` naming ``path`` when it cannot be written."""
    write_text_or_exit(path, json.dumps(payload, indent=2), code)


def _require_ragas() -> None:
    """Exit before any model call when the ragas extra is not installed."""
    if importlib.util.find_spec("ragas") is None:
        sys.exit("RAGAS meta-evaluation needs the ragas extra: pip install 'dokimi[ragas]'")


def _run_ragas_eval(answers, output_path, args) -> None:
    """Run RAGAS meta-evaluation and save results alongside answers."""
    from dokimi.eval.question_text import EmptyQuestionError
    from dokimi.eval.ragas_eval import RagasEvaluator

    ragas_model = getattr(args, "ragas_model", DEFAULT_JUDGE_MODEL)
    ragas_provider = getattr(args, "ragas_provider", DEFAULT_JUDGE_PROVIDER)

    print(f"\n🔍 Running RAGAS meta-evaluation ({ragas_provider}/{ragas_model})...")
    try:
        evaluator = RagasEvaluator(
            llm_model=ragas_model,
            provider=ragas_provider,
        )
        result = evaluator.evaluate(answers, include_without_docs=True)

        # Save ragas results alongside answers
        ragas_path = output_path.with_name(output_path.stem + "_ragas.json")
        _write_json_or_exit(ragas_path, result.to_dict(), code=2)

        print(result.format_summary())
        print(f"✅ Saved RAGAS results to {ragas_path}")

    except EmptyQuestionError:
        raise  # a bad input record is a refusal, not a RAGAS failure: cli.main reports it (exit 2)
    except ImportError as exc:
        sys.exit(f"RAGAS meta-evaluation was requested but cannot run: {exc}")
    except Exception as exc:
        print(f"\n⚠️  RAGAS evaluation failed: {exc}")


def cmd_eval_panel_score(args: argparse.Namespace) -> None:
    """Score answers using a multi-judge panel (parallel, role-diverse judges)."""
    from dokimi.eval.panel import JudgePanel, JudgeConfig, DEFAULT_PANEL, JUDGE_ROLES

    # --judge-configs takes priority over --model/--provider/--roles
    # Format: "technical_expert:claude-sonnet-5-5:anthropic,developer_advocate:claude-opus-5-5:anthropic,doc_reviewer:gemini-2.0-flash:google"
    if getattr(args, "judge_configs", None):
        judges = []
        for entry in args.judge_configs.split(","):
            parts = entry.strip().split(":")
            if len(parts) != 3:
                print(
                    f"Error: --judge-configs entry '{entry}' must be 'role:model:provider'.",
                    file=sys.stderr,
                )
                sys.exit(EXIT_USAGE)
            role, model, provider = parts
            if role not in JUDGE_ROLES:
                print(f"Error: unknown role '{role}'. Valid: {list(JUDGE_ROLES)}", file=sys.stderr)
                sys.exit(EXIT_USAGE)
            judges.append(JudgeConfig(role=role.strip(), model=model.strip(), provider=provider.strip(),
                                      temperature=args.judge_temperature))
        roles = [j.role for j in judges]
    else:
        roles = [r.strip() for r in args.roles.split(",")] if args.roles else DEFAULT_PANEL
        unknown = [r for r in roles if r not in JUDGE_ROLES]
        if unknown:
            print(f"Error: unknown roles: {unknown}. Valid: {list(JUDGE_ROLES)}", file=sys.stderr)
            sys.exit(EXIT_USAGE)
        judges = [JudgeConfig(role=r, model=args.model, provider=args.provider,
                              temperature=args.judge_temperature) for r in roles]

    panel = JudgePanel(judges=judges, concurrency=args.concurrency)

    run = _load_answers(args.answers)
    answers = list(run.records)

    answer_model = run.meta.get("model", "unknown")
    answer_provider = run.meta.get("provider", "unknown")
    if answer_model != "unknown" and answer_provider != "unknown":
        for cfg in judges:
            _warn_judge_independence(
                answer_provider=answer_provider,
                answer_model=answer_model,
                judge_provider=cfg.provider,
                judge_model=cfg.model,
                context="panel-score",
            )

    n_effective = min(len(answers), args.limit) if args.limit else len(answers)
    print(f"Panel evaluation: {n_effective} answers × {len(judges)} judges → {n_effective * len(judges)} LLM calls")
    print(f"Roles: {', '.join(roles)}\nModel: {args.provider}/{args.model}\n")

    output_path = Path(args.output) if args.output else Path(f"eval/{args.product}_panel.json")
    try:
        results = panel.evaluate_answers(answers, library_name=args.product,
                                         output_path=output_path,
                                         limit=getattr(args, "limit", None),
                                         run_metadata={"answer_model": answer_model,
                                                       "answer_provider": answer_provider})
    except OSError as exc:
        print(f"Error: cannot write {output_path}: {exc.strerror or exc}", file=sys.stderr)
        sys.exit(2)

    # Summary: the context arm's own quality, not a comparison.
    from dokimi.records import CONTEXT, records

    valid = [r.scores[CONTEXT] for r in records(results, kind="evaluations") if r.score(CONTEXT) is not None]
    if valid:
        mean_score = sum(s.aggregate or 0 for s in valid) / len(valid)
        mean_agree = sum(s.raw.get("agreement_score", 1) for s in valid) / len(valid)
        flagged = sum(1 for s in valid if s.raw.get("disagreement_flag", False))
        print(f"\n{'─'*50}")
        print(f"Panel score (context arm): {mean_score:.1f}/100")
        print(f"Mean agreement score:     {mean_agree:.3f}  (1.0 = perfect)")
        print(f"Disagreement flags:       {flagged}/{len(valid)} questions")

    print(f"\n✅ Saved to {output_path}")


def cmd_eval_score(args: argparse.Namespace) -> None:
    """Evaluate answers using LLM-as-judge."""
    from dokimi.eval import Judge

    # Load answers
    run = _load_answers(args.answers)
    answers = list(run.records)
    print(f"Loaded {len(answers)} answers from {args.answers}")

    answer_model = run.meta.get("model", "unknown")
    answer_provider = run.meta.get("provider", "unknown")
    same_model_warning = False
    if answer_model != "unknown" and answer_provider != "unknown":
        same_model_warning = _warn_judge_independence(
            answer_provider=answer_provider,
            answer_model=answer_model,
            judge_provider=args.judge_provider,
            judge_model=args.judge_model,
            context="eval-score",
        )

    try:
        method_version = answers_method_version(run.meta)
    except ValueError as exc:  # refused before any judge call is paid for
        print(f"Error: {args.answers}: {exc}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
    print(f"Evaluating with judge: {args.judge_provider}/{args.judge_model}")

    # Evaluate
    judge = Judge(
        model=args.judge_model,
        provider=args.judge_provider,
        temperature=args.judge_temperature,
        judge_placeholder=getattr(args, "judge_placeholder", False),
        # The eval says which answering method made the answers it judges; an unstamped file is legacy.
        answers_method_version=method_version,
        run_metadata={
            "question_model": run.meta.get("question_model", "unknown"),
            "question_provider": run.meta.get("question_provider", "unknown"),
            "answer_model": answer_model,
            "answer_provider": answer_provider,
            "judge_model": args.judge_model,
            "judge_provider": args.judge_provider,
            "evaluator_independence_warning": same_model_warning,
        },
    )

    if args.output:
        output_path = Path(args.output)
    elif getattr(args, "run_id", None):
        output_path = Path(f"results/{args.product.lower()}_{args.run_id}/eval/{args.product}.json")
    else:
        output_path = Path(f"eval/{args.product}.json")

    from dokimi.records import RecordError

    try:
        evaluations = judge.evaluate_answers(args.product, answers, output_path=output_path,
                                             concurrency=args.concurrency)
        judge.save_evaluations(evaluations, output_path)
    except RecordError as exc:
        print(f"Error: {args.answers}: {exc}", file=sys.stderr)
        sys.exit(2)
    except OSError as exc:
        print(f"Error: cannot write {output_path}: {exc.strerror or exc}", file=sys.stderr)
        sys.exit(2)
    print(f"\n✅ Saved evaluations to {output_path}")


def cmd_eval_ragas(args: argparse.Namespace) -> None:
    """Run standalone RAGAS meta-evaluation on an answers JSON."""
    _require_ragas()
    answers = list(_load_answers(args.answers).records)
    print(f"Loaded {len(answers)} answers from {args.answers}")

    class _Args:
        ragas_model = args.ragas_model
        ragas_provider = args.ragas_provider

    output_path = Path(args.answers)
    _run_ragas_eval(answers, output_path, _Args())


def cmd_eval_grounding(args: argparse.Namespace) -> None:
    """Compute reference-free grounding/citation metrics (no LLM calls)."""
    from dokimi.eval.grounding import evaluate_grounding

    answers = _load_answers(args.answers).records
    result = evaluate_grounding(answers, threshold=args.threshold)

    summary = result["summary"]
    gs = summary["grounding_score"]
    print(f"Grounding over {summary['n_evaluated']} context-arm answers:")
    if gs["mean"] is not None:
        print(f"  grounding_score : {gs['mean']:.3f}  (95% CI {gs['lo']:.3f}–{gs['hi']:.3f})")
    print(f"  citation_rate   : {summary['citation_rate']:.3f}  (threshold {summary['threshold']})")

    if args.output:
        out = Path(args.output)
        _write_json_or_exit(out, result, code=2)
        print(f"\n✅ Grounding report saved to {out}")


def cmd_eval_judge_validity(args: argparse.Namespace) -> None:
    """Measure whether the judge can order pairs whose ordering is known."""
    if args.mode != "pairwise" and (args.escalate_model or args.escalate_ties):
        # Silently ignoring it would report an absolute run as if escalation had been asked for.
        print("Error: --escalate-model / --escalate-ties need --mode pairwise", file=sys.stderr)
        sys.exit(2)
    if args.sequential:
        from dokimi.eval.judge_sequential import sprt_bounds

        try:
            sprt_bounds(args.min_accuracy)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
    from dokimi.eval.degrade import DEGRADATIONS
    from dokimi.eval.judge import Judge
    from dokimi.eval.judge_validity import judge_scorer, run_validity

    run = _load_answers(args.answers)
    answers = list(run.records)
    library = args.product or run.meta.get("library_name") or "the library"

    variants = [v.strip() for v in args.variants.split(",")] if args.variants else list(DEGRADATIONS)
    unknown = sorted(set(variants) - set(DEGRADATIONS))
    if unknown:
        print(f"Error: unknown degradation(s): {unknown}; known: {sorted(DEGRADATIONS)}", file=sys.stderr)
        sys.exit(2)

    # The default arm is the shipped judge. A typed arm is a local baseline that
    # reads the answer but generates nothing — a second reference line next to
    # the length-only one, not a judge replacement.
    # Built once and passed to both modes, so a new scorer kind is a change to
    # this block, not to each run path.
    if args.scorer not in ("judge", "typed") and not args.scorer.startswith("typed:"):
        print(f"Error: unknown --scorer {args.scorer!r}; use 'judge' or 'typed:<backend>'", file=sys.stderr)
        sys.exit(2)
    if args.context == "stored" and (args.scorer != "judge" or args.mode == "pairwise"):
        # Refused before any model is loaded: neither scorer has a context slot.
        what = "the typed scorer" if args.scorer != "judge" else "--mode pairwise"
        print(f"Error: --context stored needs a judge that takes context; {what} does not",
              file=sys.stderr)
        sys.exit(2)
    typed = None
    if args.scorer == "judge":
        judge = Judge(model=args.judge_model, provider=args.judge_provider, temperature=args.judge_temperature)
        scorer = judge_scorer(judge)
        label = f"{args.judge_provider}/{args.judge_model}"
    elif args.mode == "pairwise":
        # _score_pairs gives pair_scorer precedence, so the pairwise judge would grade
        # the pairs and the report would carry the typed arm's label.
        print("Error: a typed scorer reads one answer at a time; it has no --mode pairwise",
              file=sys.stderr)
        sys.exit(2)
    else:
        from dokimi.eval.degrade import build_pairs
        from dokimi.eval.typed_scorer import parse_spec, typed_scorer

        spec = args.scorer.partition(":")[2] or "laya"
        try:
            parse_spec(spec)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
        # Loading the model takes longer than building every pair, so find out
        # first whether there is anything to score.
        noop: dict = {}
        if not build_pairs(answers, n_bases=args.max_bases if args.sequential else args.bases,
                           seed=args.seed,
                           variants=variants, library=library, noop=noop):
            why = (f"every degradation left the answer unchanged (no-op pairs: {noop})"
                   if noop else "too few answers, or all too short")
            print(f"Error: the answers file yields no usable degradation pairs ({why})",
                  file=sys.stderr)
            sys.exit(2)
        try:
            typed = typed_scorer(
                spec,
                max_len=args.typed_max_len,
                threads=args.typed_threads,
            )
        except (ImportError, ValueError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
        scorer = typed
        # The budget is part of what was measured: one checkpoint at 512 and at
        # 2048 tokens is two arms.
        label = typed.name + (f" max_len={typed.effective_max_len}" if typed.effective_max_len else "")
    store = None
    if args.context == "stored":
        # The gate in the setting arms runs score in: each answer with its arm's
        # treatment material, from the artifact's judge_materials.
        from dokimi.eval.judge_context import JudgeContextStore

        # from_artifact reads the file's judge_materials, which the Run records do not keep.
        data = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        store = JudgeContextStore.from_artifact(data) if isinstance(data, dict) else None
        if store is None or not any(store.materials().values()):
            print("Error: --context stored needs an arms artifact with non-empty stored judge "
                  "material; this file has none (judge_materials missing, or no record cites a "
                  "digest that matches)", file=sys.stderr)
            sys.exit(2)
    pair_scorer = None
    if args.mode == "pairwise":
        from dokimi.commands.judge_reliability import pairwise_judge

        pairwise, label = pairwise_judge(args)
        pair_scorer = pairwise.pair_scorer()
    if args.sequential:
        print(f"Judge validity check: {label}, sequential — up to {args.max_bases} base answers "
              f"x {len(variants)} variants, stopping each when decided")
    else:
        print(f"Judge validity check: {label}, {args.bases} base answers x {len(variants)} variants")

    def progress(done: int, total: int) -> None:
        if done % 10 == 0 or done == total:
            print(f"  [{done}/{total}]", flush=True)

    common = dict(
        judge=label, seed=args.seed, variants=variants, library=library,
        min_accuracy=args.min_accuracy, progress=progress,
        # One model is shared, and usage["seconds"] is summed over calls: on a pool it
        # overstates the cost per score by the pool size.
        concurrency=1 if typed is not None else args.concurrency,
        pair_scorer=pair_scorer,
        store=store,
    )
    try:
        if args.sequential:
            from dokimi.eval.judge_sequential import run_sequential

            report = run_sequential(answers, scorer, max_bases=args.max_bases, **common)
        else:
            report = run_validity(answers, scorer, n_bases=args.bases, **common)
    except NoUsablePairs as exc:  # none long enough, all no-ops, none with material
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    # Written before anything is printed: a console that cannot encode the summary must not lose the run.
    if args.output:
        out = Path(args.output)
        result = {**report.to_dict(), "judge_temperature": args.judge_temperature}
        # Which answers the gate was measured on: a judge that passes on one
        # answer model's output is not thereby validated on another's.
        result["summary"]["answers_model"] = run.meta.get("model")
        if typed is not None:
            result["summary"]["typed_arm"] = typed.report_fields()
        _write_json_or_exit(out, result, code=2)

    print()
    print(report.format_summary())
    if typed is not None:
        print(f"\n  arm cost: {typed.usage_summary()}")
    if args.output:
        print(f"\n✅ Validity report saved to {out}")

    summary = report.summary()
    if not summary["passed"]:
        sys.exit(1)
    # A pass whose interval straddles the gate is not reproducible. Warn by
    # default so this stays usable as a check; --strict makes it a failure.
    if not summary["conclusive"]:
        if summary["inconclusive_variants"]:
            # --sequential ignores --bases, so pointing at it would send the user nowhere.
            more = "Raise --max-bases or add answers (see above)." if args.sequential else "Re-run with more --bases."
            print(
                "\n⚠️  Pass is inconclusive at this sample size: "
                f"{', '.join(summary['inconclusive_variants'])}. {more}",
                file=sys.stderr,
            )
        if summary["unmeasured_variants"]:
            print(
                f"\n⚠️  Pass is inconclusive: no pair was built for {', '.join(summary['unmeasured_variants'])} "
                "(the degradation left every answer unchanged).",
                file=sys.stderr,
            )
        if args.strict:
            sys.exit(1)


def _non_negative_int(value: str) -> int:
    """argparse type: an int >= 0 (0 means "the checkpoint's own default")."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if n < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {n}")
    return n


def register(sub, positive_int) -> None:
    """Add the `eval` subcommand group."""
    # Eval subcommand group
    eval_p = sub.add_parser("eval", help="Evaluate answers")
    eval_sub = eval_p.add_subparsers(dest="eval_cmd", required=True)

    # eval score
    score_p = eval_sub.add_parser("score", help="Score answers using LLM-as-judge")
    score_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    score_p.add_argument("--answers", required=True, help="Path to answers JSON file")
    score_p.add_argument("--run-id", default=None, dest="run_id",
                         help="Run tag (e.g. gpt4o). Sets output to results/{product}_{run_id}/eval/{product}.json")
    score_p.add_argument("--output", default=None, help="Output file (default: eval/{product}.json)")
    score_p.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    score_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    score_p.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY, help="Parallel judge calls (default: 5)")
    score_p.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                         help="Judge sampling temperature, recorded as judge_temperature in the output")
    score_p.add_argument("--judge-placeholder", action="store_true", dest="judge_placeholder",
                         help="Judge the no-retrieval placeholder answers too (default: they are not scored)")
    score_p.set_defaults(func=cmd_eval_score)

    # eval ragas
    ragas_p = eval_sub.add_parser("ragas", help="Run RAGAS meta-evaluation on answers")
    ragas_p.add_argument("--answers", required=True, help="Path to answers JSON file")
    ragas_p.add_argument("--ragas-model", default=DEFAULT_JUDGE_MODEL,
                         help=f"LLM for RAGAS judge (default: {DEFAULT_JUDGE_MODEL})")
    ragas_p.add_argument("--ragas-provider", default=DEFAULT_JUDGE_PROVIDER, choices=["openai", "anthropic"],
                         help=f"Provider for RAGAS judge LLM (default: {DEFAULT_JUDGE_PROVIDER})")
    ragas_p.set_defaults(func=cmd_eval_ragas)

    # eval grounding (reference-free, no LLM)
    grounding_p = eval_sub.add_parser(
        "grounding", help="Reference-free grounding/citation metrics with confidence intervals"
    )
    grounding_p.add_argument("--answers", required=True, help="Path to answers JSON file")
    grounding_p.add_argument("--output", default=None, help="Output JSON report path")
    grounding_p.add_argument("--threshold", type=float, default=0.5,
                             help="Grounding score above which an answer counts as grounded (default: 0.5)")
    grounding_p.set_defaults(func=cmd_eval_grounding)

    # eval judge-validity (does the judge order known-worse answers below better ones?)
    validity_p = eval_sub.add_parser(
        "judge-validity",
        help="Measure judge discriminative validity on deterministic degradations (exits 1 on FAIL)",
    )
    validity_p.add_argument("--answers", required=True, help="Path to answers JSON file")
    validity_p.add_argument("--product", default=None, help="Library name (default: read from answers file)")
    validity_p.add_argument("--bases", type=positive_int, default=20,
                            help="Number of base answers to degrade (default: 20)")
    validity_p.add_argument("--variants", default=None,
                            help="Comma-separated degradations (default: all)")
    validity_p.add_argument("--seed", type=int, default=0, help="Seed for the degradations (default: 0)")
    validity_p.add_argument("--min-accuracy", type=float, default=DEFAULT_MIN_ACCURACY, dest="min_accuracy",
                            help=f"Pairwise accuracy gate, per degradation (default: {DEFAULT_MIN_ACCURACY}); "
                                 "with --sequential it must lie in 0.03-0.97")
    validity_p.add_argument("--strict", action="store_true",
                            help="Also exit 1 when a PASS is inconclusive — its 95%% CI "
                                 "straddles the gate, so the run cannot tell it from a FAIL")
    validity_p.add_argument("--sequential", action="store_true",
                            help="Add bases in rounds and stop each degradation once an SPRT can call "
                                 "it PASS or FAIL (ignores --bases; see --max-bases)")
    validity_p.add_argument("--max-bases", type=positive_int, default=120, dest="max_bases",
                            help="Cap on base answers in --sequential mode (default: 120)")
    validity_p.add_argument("--context", choices=["none", "stored"], default="none",
                            help="What the judge sees with each answer: none (default), or 'stored' — "
                                 "the arm's treatment material from the artifact's judge_materials, the "
                                 "setting an arms run scores skill and docs arms in. Exits 2 with a typed "
                                 "scorer or --mode pairwise, with no verified material, when an arm "
                                 "loses all its bases, has only padding pairs or gets none sampled, when a --sequential run "
                                 "would pass with none of an arm's bases scored, or when no pair is usable")
    validity_p.add_argument("--mode", choices=["absolute", "pairwise"], default="absolute",
                            help="absolute: score each member 0-100 (the shipped judge); pairwise: show "
                                 "the judge both members, in both orders, and ask which is better")
    validity_p.add_argument("--escalate-model", default=None, dest="escalate_model",
                            help="With --mode pairwise: re-ask pairs the judge answered inconsistently across the two orders with this model")
    validity_p.add_argument("--escalate-provider", default=None, dest="escalate_provider", choices=JUDGE_PROVIDERS,
                            help="Provider for --escalate-model (default: --judge-provider)")
    validity_p.add_argument("--escalate-ties", action="store_true", dest="escalate_ties",
                            help="Also escalate pairs called TIE in both orders (default: only order-inconsistent)")
    validity_p.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY,
                            help=f"Parallel judge calls (default: {DEFAULT_CONCURRENCY})")
    validity_p.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    validity_p.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    validity_p.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                            help="Judge sampling temperature; match arms run --judge-temperature to certify the same judge")
    validity_p.add_argument("--scorer", default="judge",
                            help="Arm to grade: 'judge' (default, the shipped LLM judge) or "
                                 "'typed:<backend>[:<repo>]' for a local typed-decision "
                                 "baseline arm — needs the [typed-scorer] extra")
    validity_p.add_argument("--typed-max-len", type=_non_negative_int, default=0, dest="typed_max_len",
                            help="Token budget for a typed arm's state (default: the "
                                 "checkpoint's own, 512 for laya English — answers are "
                                 "longer than that and get truncated)")
    validity_p.add_argument("--typed-threads", type=positive_int, default=None, dest="typed_threads",
                            help="CPU threads for a typed arm (default: torch's own)")
    validity_p.add_argument("--output", default=None, help="Output JSON report path")
    validity_p.set_defaults(func=cmd_eval_judge_validity)

    from dokimi.commands import judge_reliability

    judge_reliability.register(eval_sub, positive_int)  # eval checklist, eval judge-wording

    from dokimi.commands import answer_formats

    answer_formats.register_eval(eval_sub, positive_int)  # eval reference, eval native-import

    # eval panel-score
    panel_p = eval_sub.add_parser("panel-score", help="Score answers using a multi-judge panel")
    panel_p.add_argument("--answers", required=True, help="Path to answers JSON file")
    panel_p.add_argument("--product", required=True, help="Product name (e.g., oneTBB)")
    panel_p.add_argument("--output", default=None, help="Output file (default: eval/{product}_panel.json)")
    panel_p.add_argument("--model", default=DEFAULT_JUDGE_MODEL, help="Default LLM model for all judges")
    panel_p.add_argument("--provider", default=DEFAULT_JUDGE_PROVIDER, choices=ANSWER_PROVIDERS)
    panel_p.add_argument("--roles", default=None,
                         help="Comma-separated judge roles (default: technical_expert,developer_advocate,doc_reviewer)")
    panel_p.add_argument(
        "--judge-configs", default=None, dest="judge_configs",
        help=(
            "Per-role model config (overrides --model/--provider/--roles). "
            "Format: 'role:model:provider,...' "
            "e.g. 'technical_expert:claude-sonnet-5-5:anthropic,"
            "developer_advocate:claude-opus-5-5:anthropic,"
            "doc_reviewer:gemini-2.0-flash:google'"
        ),
    )
    panel_p.add_argument("--concurrency", type=positive_int, default=6,
                         help="Parallel judge API calls (default: 6)")
    panel_p.add_argument("--limit", type=positive_int, default=None,
                         help="Evaluate only first N answers (useful for testing)")
    panel_p.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                         help="Sampling temperature for every panel judge, recorded as judge_temperature")
    panel_p.set_defaults(func=cmd_eval_panel_score)
