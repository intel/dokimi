# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval checklist / eval judge-wording: the judge-reliability checks beyond judge-validity."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from dokimi.defaults import (
    DEFAULT_CONCURRENCY,
    DEFAULT_JUDGE_PROVIDER,
    JUDGE_MODEL_FOR_PROVIDER,
    JUDGE_MODEL_HELP,
)
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.eval.judge_validity import DEFAULT_MIN_ACCURACY


def _read_json(path: str):
    """The parsed JSON at ``path``, or exit 2 (bad input, not a failed gate) naming the file."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError covers JSONDecodeError and bad UTF-8
        print(f"Error: cannot read {path}: {exc}", file=sys.stderr)
        sys.exit(2)


def _load_json_object(path: str, key: Optional[str] = None) -> dict:
    """The JSON object at ``path``, or exit 2 naming the file.

    With ``key``, the object must also hold an object under that key.
    """
    data = _read_json(path)
    if not isinstance(data, dict) or (key is not None and not isinstance(data.get(key), dict)):
        what = "a JSON object" if key is None else f"a JSON object with a `{key}` object"
        print(f"Error: {path} is not {what}", file=sys.stderr)
        sys.exit(2)
    return data


def _load_answers(path: str):
    """The parsed file and its answers records (dokimi/records.py), or a clean exit."""
    from dokimi.records import RecordError, read

    data = _read_json(path)
    try:
        answers = list(read(data, source=path, section="answers").records)
    except RecordError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)  # 1 is the verdict code of judge-validity and the --strict gates
    return data, answers


def _require_arms(path: str, records, command: str) -> None:
    """Exit 2, before any judge call, when no answers record holds ``arms``.

    The arm judges read an arms run; a binary answers file's ``with_docs`` and
    ``without_docs`` are not arms to them, so such a file would judge nothing.
    """
    from dokimi.records import has_arms

    if not has_arms(records):
        print(f"Error: {path} has no arms answers (no record has an `arms` field); "
              f"eval {command} reads an arms run (arms run --out-json), not a binary "
              "with_docs/without_docs answers file", file=sys.stderr)
        sys.exit(2)


def _progress(label: str = ""):
    def cb(done: int, total: int) -> None:
        if done % 25 == 0 or done == total:
            print(f"  {label}[{done}/{total}]", flush=True)
    return cb


def _check_judge_temperature(judges: list[tuple[str, str]], temperature) -> None:
    """Exit 2 before any judgement when a (model, provider) judge rejects ``temperature``."""
    if temperature is None:
        return
    from dokimi.commands.arms import _probe_sampling
    from dokimi.llm import unsupported_sampling_params
    from dokimi.utils import bedrock_litellm_model

    for model, provider in dict.fromkeys(judges):
        # A Bedrock judge goes through get_llm, which maps a bare name first.
        resolved = bedrock_litellm_model(model) if provider == "amazon-bedrock" else model
        try:
            if unsupported_sampling_params(resolved, provider, temperature=temperature):
                raise ValueError(f"provider '{provider}' does not support temperature for model '{model}'")
            _probe_sampling(resolved, provider, temperature, None)
        except ValueError as exc:
            print(f"Error: --judge-temperature {temperature} is rejected by judge {provider}/{model}: {exc}",
                  file=sys.stderr)
            sys.exit(2)


def pairwise_judge(args: argparse.Namespace):
    """A both-order pairwise judge from --judge-* and optional --escalate-* args; returns (judge, label)."""
    from dokimi.eval.judge import Judge
    from dokimi.eval.pairwise import PairwiseJudge

    temperature = getattr(args, "judge_temperature", None)

    def asker(model: str, provider: str):
        llm = Judge(model=model, provider=provider, temperature=temperature).llm
        return lambda prompt, draw=0, cache_if=None: llm.invoke(prompt, draw=draw, cache_if=cache_if).content

    label = f"{args.judge_provider}/{args.judge_model} pairwise"
    judges = [(args.judge_model, args.judge_provider)]
    escalate = None
    if getattr(args, "escalate_model", None):
        provider = args.escalate_provider or args.judge_provider
        judges.append((args.escalate_model, provider))
        label += f" → {provider}/{args.escalate_model}"
    _check_judge_temperature(judges, temperature)
    if len(judges) > 1:
        escalate = asker(*judges[1])
    judge = PairwiseJudge(ask=asker(args.judge_model, args.judge_provider), escalate=escalate,
                          escalate_ties=bool(getattr(args, "escalate_ties", False)))
    return judge, label


def cmd_eval_pairwise(args: argparse.Namespace) -> None:
    """Every arm against the baseline, both answers shown together, both orders."""
    from dokimi.eval.pairwise import pairwise_jobs, run_arm_pairwise

    data, records = _load_answers(args.answers)
    baseline = args.baseline_arm or (data.get("baseline_arm") if isinstance(data, dict) else None) or "baseline"
    library = args.product or (data.get("library_name") if isinstance(data, dict) else None) or "the library"
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else None
    _require_arms(args.answers, records, "pairwise")
    pairwise_jobs(records, baseline_arm=baseline, arms=arms)  # refuse a blank question before the probe call
    judge, label = pairwise_judge(args)
    print(f"Pairwise comparison: {label}, {len(records)} questions, baseline `{baseline}`")
    report = run_arm_pairwise(
        records, judge, label=label, library=library, baseline_arm=baseline, arms=arms,
        concurrency=args.concurrency, progress=_progress(),
    )
    if not report.verdicts and not report.errors:
        print(f"Error: no question has both a `{baseline}` answer and another arm's", file=sys.stderr)
        sys.exit(2)
    md = report.format_markdown()
    out = Path(args.output or Path(args.answers).with_name(Path(args.answers).stem + "_pairwise.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**report.to_dict(), "judge_temperature": getattr(args, "judge_temperature", None)},
                              indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    if not report.verdicts:
        # Written first, so the reasons are not lost with the run.
        print(f"Error: all {len(report.errors)} comparison(s) failed; see `errors` in {out}", file=sys.stderr)
        sys.exit(2)
    print()
    print(md)
    print(f"✅ Pairwise report saved to {out} (+ .md)")


def cmd_eval_checklist(args: argparse.Namespace) -> None:
    """Required-point coverage of every arm, against the questions' expected_topics."""
    from dokimi.eval.checklist import checklist_jobs, load_points, run_checklist
    from dokimi.eval.judge import Judge

    data, records = _load_answers(args.answers)
    points = load_points(args.questions)
    baseline = args.baseline_arm or (data.get("baseline_arm") if isinstance(data, dict) else None) or "baseline"
    arms = [a.strip() for a in args.arms.split(",")] if args.arms else None
    _require_arms(args.answers, records, "checklist")
    checklist_jobs(records, points, arms=arms)  # refuse a blank question before the probe call
    _check_judge_temperature([(args.judge_model, args.judge_provider)], args.judge_temperature)
    llm = Judge(model=args.judge_model, provider=args.judge_provider, temperature=args.judge_temperature).llm
    label = f"{args.judge_provider}/{args.judge_model}"
    print(f"Required-point coverage: {label}, {len(records)} questions, baseline `{baseline}`")

    report = run_checklist(
        records, points,
        lambda prompt, draw=0, cache_if=None: llm.invoke(prompt, draw=draw, cache_if=cache_if).content,
        judge=label,
        baseline_arm=baseline, arms=arms, concurrency=args.concurrency, progress=_progress(),
    )
    if not report.verdicts:
        print("Error: no question in the answers file has expected_topics in --questions", file=sys.stderr)
        sys.exit(2)
    out = Path(args.output or Path(args.answers).with_name(Path(args.answers).stem + "_checklist.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    # The JSON first: the paid calls are in it, and a rendering error must not lose them.
    out.write_text(json.dumps({**report.to_dict(), "judge_temperature": args.judge_temperature}, indent=2),
                   encoding="utf-8")
    md = report.format_markdown()
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print()
    print(md)
    print(f"✅ Coverage report saved to {out} (+ .md)")


def cmd_eval_judge_wording(args: argparse.Namespace) -> None:
    """Judge-validity under meaning-preserving rewrites of the judge prompt."""
    from dokimi.eval.degrade import DEGRADATIONS
    from dokimi.eval.judge import JUDGE_PROMPT, Judge
    from dokimi.eval.judge_validity import _build, judge_scorer
    from dokimi.eval.judge_wording import WORDINGS, run_wording

    data, answers = _load_answers(args.answers)
    library = args.product or (data.get("library_name") if isinstance(data, dict) else None) or "the library"
    variants = [v.strip() for v in args.variants.split(",")] if args.variants else list(DEGRADATIONS)
    wordings = [w.strip() for w in args.wordings.split(",")] if args.wordings else list(WORDINGS)
    unknown = sorted(set(wordings) - set(WORDINGS))
    if unknown:
        print(f"Error: unknown wording(s): {unknown}; known: {sorted(WORDINGS)}", file=sys.stderr)
        sys.exit(2)
    pairs = _build(answers, {}, n_bases=args.bases, seed=args.seed, variants=variants, library=library).pairs
    label = f"{args.judge_provider}/{args.judge_model}"
    n_cond = 1 + args.redraws + len(wordings)
    print(f"Judge wording sensitivity: {label}, {len(pairs)} pairs x {n_cond} conditions "
          f"(original, {args.redraws} re-draw(s), {', '.join(wordings)})")

    _check_judge_temperature([(args.judge_model, args.judge_provider)], args.judge_temperature)

    def scorer_for(template, draw):
        return judge_scorer(Judge(model=args.judge_model, provider=args.judge_provider,
                                  temperature=args.judge_temperature, prompt_template=template), draw=draw)

    def progress(cond: str, done: int, total: int) -> None:
        if done == total:
            print(f"  {cond}: {total} pairs", flush=True)

    report = run_wording(
        pairs, scorer_for, JUDGE_PROMPT, judge=label, wordings=wordings, redraws=args.redraws,
        min_accuracy=args.min_accuracy, seed=args.seed, concurrency=args.concurrency, progress=progress,
    )
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({**report.to_dict(), "judge_temperature": args.judge_temperature}, indent=2),
                       encoding="utf-8")
    print()
    print(report.format_summary())
    if args.output:
        print(f"\n✅ Wording report saved to {out}")
    if args.strict and report.summary()["fragile_variants"]:
        sys.exit(1)


def cmd_eval_cross_judge(args: argparse.Namespace) -> None:
    """Score the same arms under several judges and label each arm's verdict."""
    from dokimi.eval.cross_judge import (
        CrossJudgeReport, coverage_measure, pairwise_measure, score_measure, scored_answers,
        stored_measure, unreproducible_arms,
    )
    from dokimi.eval.arm_runner import _ground_truth_from_topics
    from dokimi.eval.checklist import load_points
    from dokimi.eval.judge import Judge

    data, records = _load_answers(args.answers)
    truth: dict[str, str] | None = None
    if args.grounded:
        if not args.questions:
            print("Error: --grounded needs --questions (their expected_topics are the ground truth)",
                  file=sys.stderr)
            sys.exit(2)
        truth = {q: t for q, pts in load_points(args.questions).items()
                 if (t := _ground_truth_from_topics({"expected_topics": pts}))}

    def scorer_of(judge):
        def score(library, question, answer, ground_truth, context):
            return judge.score_answer(library_name=library, question=question, answer=answer,
                                      context=context, ground_truth=ground_truth)["aggregate"]
        return score

    baseline = args.baseline_arm or (data.get("baseline_arm") if isinstance(data, dict) else None) or "baseline"
    library = args.product or (data.get("library_name") if isinstance(data, dict) else None) or "the library"
    report = CrossJudgeReport(baseline_arm=baseline, require_validated=args.require_validated)
    from dokimi.eval.judge_context import SOURCE_STORED, JudgeContextStore
    from dokimi.utils import question_id

    # What the arms run's judge saw, when the file keeps it (judge_materials). Built
    # once and only for --judges: it is the store the gate checks and the judge reads.
    materials: Optional[JudgeContextStore] = (
        JudgeContextStore.from_artifact(data) if args.judges and isinstance(data, dict) else None
    )

    from dokimi.records import RecordError

    try:
        stored = None if args.no_stored or not isinstance(data, dict) else stored_measure(data)
        pair_unrepro = unreproducible_arms(data) if stored and (args.pairwise or args.checklist) else []
    except RecordError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)  # bad input, not a failed gate
    if (m := stored):
        # Re-scored judges get the file's judge_materials; pairwise and checklist
        # judges see the answers alone. Each path sees different input, so we refuse
        # the comparison to prevent mixing a change of judge with a change of input.
        unrepro: set[str] = set()
        # Per arm, the questions where the re-scored judge would read a different
        # text than the stored judge (differs), or none at all (unserved).
        differs: dict[str, list] = {}
        unserved: dict[str, list] = {}

        # Pairwise and checklist judges see only answers, so stored material makes them incompatible
        if args.pairwise or args.checklist:
            unrepro.update(pair_unrepro)
        elif materials is not None:
            # The loop below is the whole --judges gate: it compares each stored score's
            # digest with what the reader serves, for the questions score_measure scores.
            # score_measure only scores questions that have an answer record.
            answered = {question_id(r) for r in data.get("answers") or []
                        if isinstance(r, dict) and isinstance(question_id(r), (str, int))}
            # The arm exempt from the check is the one whose recorded kind is baseline, as in
            # unreproducible_arms: --baseline-arm only picks the comparison's reference arm.
            kinds = {arm: (body.get("metadata") or {}).get("arm_kind")
                     for r in data.get("answers") or []
                     if isinstance(r, dict) and isinstance(r.get("arms"), dict)
                     for arm, body in r["arms"].items() if isinstance(body, dict)}
            for ev in data.get("evaluations") or []:
                if not isinstance(ev, dict):
                    continue
                qid = ev.get("question_id")
                for arm, s in (ev.get("scores") or {}).items():
                    kind = kinds.get(arm) or ("baseline" if arm == baseline else None)
                    if not (isinstance(s, dict) and s.get("judge_context_source") == SOURCE_STORED
                            and kind != "baseline"):
                        continue
                    if not isinstance(qid, (str, int)):
                        unserved.setdefault(arm, []).append(qid)  # no usable id: reader uses snippets
                    elif qid not in answered:
                        continue
                    elif (served := materials.digest_of(qid, arm)) is None:
                        unserved.setdefault(arm, []).append(qid)
                    elif s.get("judge_context_sha256") != served:
                        differs.setdefault(arm, []).append(qid)

        if unrepro:
            print(f"Error: the stored judge saw treatment material for {', '.join(sorted(unrepro))}, but a "
                  "pairwise or checklist judge sees only the answers, not the arm's treatment material, "
                  "so the comparison would mix a change of judge with a change of input. Pass "
                  "--no-stored to compare them with each other.", file=sys.stderr)
        for arm, qids in sorted(differs.items()):
            print(f"Error: for {arm}, the stored score cites different material than the arm record "
                  f"serves a re-scored judge, for question(s) {', '.join(map(repr, qids))}: the "
                  "re-scored judge would read a different text. Pass --no-stored to compare "
                  "them with each other.", file=sys.stderr)
        for arm, qids in sorted(unserved.items()):
            print(f"Error: for {arm}, no usable material can be read for question(s) "
                  f"{', '.join(map(repr, qids))} (the record cites no digest, the text fails its "
                  "digest, or the question has no usable id), so a re-scored judge would read "
                  "snippets where the stored judge read the material. Pass --no-stored to "
                  "compare them with each other.", file=sys.stderr)
        if unrepro or differs or unserved:
            sys.exit(2)
        report.measures.append(m)

    specs = [j.strip() for j in (args.judges or "").split(",") if j.strip()]
    if specs:
        _require_arms(args.answers, records, "cross-judge --judges")
        scored_answers(records)  # refuse a blank question before the probe call
    _check_judge_temperature([(m, p) for p, _, m in (s.partition("/") for s in specs) if m and p in JUDGE_PROVIDERS],
                             args.judge_temperature)
    for spec in specs:
        provider, _, model = spec.partition("/")
        if not model or provider not in JUDGE_PROVIDERS:
            print(f"Error: --judges entry '{spec}' must be provider/model, provider in {JUDGE_PROVIDERS}",
                  file=sys.stderr)
            sys.exit(2)
        print(f"Scoring with {spec} ...", flush=True)
        judge = Judge(model=model, provider=provider, temperature=args.judge_temperature)
        for grounding in ([None, truth] if truth and args.both else [truth]):
            report.measures.append(score_measure(
                records, scorer_of(judge), name=f"{provider}/{model}", library=library,
                concurrency=args.concurrency, progress=_progress(), ground_truth=grounding,
                store=materials,
            ))
    for path in args.pairwise or []:
        pw = _load_json_object(path)
        name = (pw.get("summary") or {}).get("judge", Path(path).stem)
        report.measures.append(pairwise_measure(pw, name, baseline))
    for path in args.checklist or []:
        ck = _load_json_object(path)
        name = (ck.get("summary") or {}).get("judge", Path(path).stem)
        report.measures.append(coverage_measure(ck, name))
    validity = [_load_json_object(v, key="summary")["summary"] for v in args.validity or []]
    answers_model = data.get("model") if isinstance(data, dict) else None
    for v in validity:
        if v.get("answers_model") and answers_model and v["answers_model"] != answers_model:
            print(f"⚠️  --validity for `{v.get('judge')}` was measured on {v['answers_model']} answers, "
                  f"these are {answers_model}'s — the gate may not transfer", file=sys.stderr)
    for missed in report.apply_validity(validity):
        print(f"⚠️  --validity for `{missed}` matches no measure (compare the judge labels and contexts)", file=sys.stderr)
    if not report.measures:
        print("Error: nothing to compare — give --judges, --checklist, or an answers file with evaluations",
              file=sys.stderr)
        sys.exit(2)
    md = report.format_markdown()
    out = Path(args.output or Path(args.answers).with_name(Path(args.answers).stem + "_crossjudge.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**report.to_dict(), "judge_temperature": args.judge_temperature}, indent=2),
                   encoding="utf-8")
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print()
    print(md)
    print(f"✅ Cross-judge report saved to {out} (+ .md)")
    if args.strict and any(a["label"] == "JUDGE-DEPENDENT" for a in report.summary()["arms"].values()):
        sys.exit(1)


def register(eval_sub, positive_int) -> None:
    """Add `eval checklist`, `eval judge-wording` and `eval cross-judge` to the eval group."""
    ck = eval_sub.add_parser(
        "checklist",
        help="Required-point coverage per arm: is each expected_topic in the answer? (both polarities)",
    )
    ck.add_argument("--answers", required=True, help="Arms output JSON (arms run --out-json)")
    ck.add_argument("--questions", required=True, help="Questions JSON whose expected_topics are the points")
    ck.add_argument("--baseline-arm", default=None, dest="baseline_arm",
                    help="Arm the others are compared to (default: the run's baseline_arm)")
    ck.add_argument("--arms", default=None, help="Comma-separated arms to check (default: all)")
    ck.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    ck.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    ck.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                    help="Judge sampling temperature, recorded as judge_temperature in the output")
    ck.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY)
    ck.add_argument("--output", default=None,
                    help="Output JSON (default: <answers>_checklist.json); Markdown written next to it")
    ck.set_defaults(func=cmd_eval_checklist)

    jw = eval_sub.add_parser(
        "judge-wording",
        help="Judge-validity under rewrites of the judge prompt, paired against the original on the same pairs",
    )
    jw.add_argument("--answers", required=True, help="Path to answers JSON file")
    jw.add_argument("--product", default=None, help="Library name (default: read from answers file)")
    jw.add_argument("--bases", type=positive_int, default=20, help="Base answers to degrade (default: 20)")
    jw.add_argument("--variants", default=None, help="Comma-separated degradations (default: all)")
    jw.add_argument("--wordings", default=None, help="Comma-separated rewrites (default: all)")
    jw.add_argument("--redraws", type=int, default=1,
                    help="Re-draws of the original prompt, shown as a noise floor; the FRAGILE "
                         "flag is a paired test against the original and does not use them (default: 1)")
    jw.add_argument("--seed", type=int, default=0)
    jw.add_argument("--min-accuracy", type=float, default=DEFAULT_MIN_ACCURACY, dest="min_accuracy")
    jw.add_argument("--strict", action="store_true", help="Exit 1 when any variant is FRAGILE")
    jw.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    jw.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    jw.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                    help="Judge sampling temperature, recorded as judge_temperature in the output")
    jw.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY)
    jw.add_argument("--output", default=None, help="Output JSON report path")
    jw.set_defaults(func=cmd_eval_judge_wording)

    cj = eval_sub.add_parser(
        "cross-judge",
        help="Each arm's delta under several judges and axes, labelled CONSISTENT / JUDGE-DEPENDENT / ...",
    )
    cj.add_argument("--answers", required=True, help="Arms output JSON (arms run --out-json)")
    cj.add_argument("--judges", default=None,
                    help="Comma-separated provider/model judges to re-score with, e.g. "
                         "amazon-bedrock/us.anthropic.claude-haiku-5-5,amazon-bedrock/openai.gpt-oss-120b-1:0")
    cj.add_argument("--checklist", action="append", default=None,
                    help="An `eval checklist` JSON to include as a coverage measure (repeatable)")
    cj.add_argument("--pairwise", action="append", default=None,
                    help="An `eval pairwise` JSON to include as a measure (repeatable)")
    cj.add_argument("--validity", action="append", default=None,
                    help="An `eval judge-validity` JSON; that judge's measures are marked PASS/FAIL and a "
                         "FAILed judge does not vote in the verdict (repeatable)")
    cj.add_argument("--require-validated", action="store_true", dest="require_validated",
                    help="Only judges that PASSED a --validity gate vote in the verdict")
    cj.add_argument("--questions", default=None, help="Questions JSON; needed for --grounded")
    cj.add_argument("--grounded", action="store_true",
                    help="Re-score against each question's expected_topics as ground truth")
    cj.add_argument("--both", action="store_true",
                    help="With --grounded, keep the ungrounded score as a measure too")
    cj.add_argument("--no-stored", action="store_true", dest="no_stored",
                    help="Leave out the scores the arms run itself recorded")
    cj.add_argument("--product", default=None, help="Library name (default: read from answers file)")
    cj.add_argument("--baseline-arm", default=None, dest="baseline_arm")
    cj.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                    help="Judge sampling temperature, recorded as judge_temperature in the output")
    cj.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY)
    cj.add_argument("--strict", action="store_true", help="Exit 1 when any arm is JUDGE-DEPENDENT")
    cj.add_argument("--output", default=None,
                    help="Output JSON (default: <answers>_crossjudge.json); Markdown written next to it")
    cj.set_defaults(func=cmd_eval_cross_judge)

    pw = eval_sub.add_parser(
        "pairwise",
        help="Every arm vs the baseline, both answers shown together in both orders; optional escalation",
    )
    pw.add_argument("--answers", required=True, help="Arms output JSON (arms run --out-json)")
    pw.add_argument("--product", default=None, help="Library name (default: read from answers file)")
    pw.add_argument("--baseline-arm", default=None, dest="baseline_arm")
    pw.add_argument("--arms", default=None, help="Comma-separated arms to compare (default: all)")
    pw.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    pw.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    pw.add_argument("--judge-temperature", type=float, default=None, dest="judge_temperature",
                    help="Judge sampling temperature, recorded as judge_temperature in the output")
    pw.add_argument("--escalate-model", default=None, dest="escalate_model",
                    help="Re-ask questions the judge left undecided (tie or order-inconsistent) with this model")
    pw.add_argument("--escalate-provider", default=None, dest="escalate_provider", choices=JUDGE_PROVIDERS)
    pw.add_argument("--escalate-ties", action="store_true", dest="escalate_ties",
                    help="Also escalate questions the judge called TIE in both orders (default: only "
                         "order-inconsistent ones)")
    pw.add_argument("--concurrency", type=positive_int, default=DEFAULT_CONCURRENCY)
    pw.add_argument("--output", default=None,
                    help="Output JSON (default: <answers>_pairwise.json); Markdown written next to it")
    pw.set_defaults(func=cmd_eval_pairwise)
