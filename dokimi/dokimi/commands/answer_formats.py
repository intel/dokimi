# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`questions import`, `eval reference` and `eval native-import`: answer-only eval formats.

Inspect AI, OpenAI evals, skill-creator evals.json and promptfoo sets are imported as
questions.v2 with ``grading`` checks, answered by the answer track (``answers generate`` or
``arms run``) and scored by ``eval reference``. A native tool's own log is read by
``eval native-import`` so its completions and verdicts sit next to dokimi's.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def cmd_questions_import(args: argparse.Namespace) -> None:
    from dokimi.questions.artifact import save_questions_artifact
    from dokimi.task_formats.imports import import_path

    options = {k: getattr(args, k) for k in ("scorer", "task", "grader") if getattr(args, k)}
    try:
        imported = import_path(Path(args.path), fmt=args.format, **options)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    save_questions_artifact(imported.artifact(), args.output)
    print(json.dumps(imported.report(), indent=2))
    print(f"Wrote {len(imported.questions)} questions to {args.output}")


def _ask(args: argparse.Namespace):
    if args.no_judge:
        return None
    from dokimi.eval.judge import judge_ask

    return judge_ask(args.judge_model, args.judge_provider)


def cmd_eval_reference(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.eval.reference_run import artifact, score
    from dokimi.questions.artifact import load_questions

    questions = load_questions(args.questions).artifact.get("questions") or []
    data = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    try:
        result = score(data, questions, ask=_ask(args), source=args.answers, concurrency=args.concurrency)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    if not result.evaluations:
        print("Error: no answered question has a grading block in --questions", file=sys.stderr)
        sys.exit(2)
    judge_model = None if args.no_judge else args.judge_model
    judge_provider = None if args.no_judge else args.judge_provider
    kind, payload = artifact(result, data, judge_model=judge_model, judge_provider=judge_provider,
                             questions_path=str(args.questions))
    save_artifact(kind, payload, args.output)
    for name, tally in result.tally.items():
        print(f"{name}: {json.dumps(tally.as_dict())}")
    if result.missing:
        print(f"{len(result.missing)} answered questions had no grading block")
    print(f"Wrote {kind} artifact to {args.output}")


def cmd_eval_native_import(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.task_formats.imports.native_logs import read_log

    try:
        run = read_log(Path(args.log))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    save_artifact("answers", run.answers_artifact(), args.answers_out)
    if args.eval_out:
        save_artifact("eval", run.eval_artifact(), args.eval_out)
    from dokimi.records import records

    # A native run has one condition per record, so every score is that run's verdict.
    scores = [s for rec in records(run.evaluations, kind="evaluations") for s in rec.scores.values()]
    passed = [s.raw["passed"] for s in scores if s.raw.get("passed") is not None]
    rate = f"{sum(passed)}/{len(passed)}" if passed else "n/a"
    print(f"{run.tool} ({run.model}): {len(run.answers)} samples, native pass {rate}")


def register_questions(questions_sub) -> None:
    p = questions_sub.add_parser(
        "import",
        help="Import an answer-only eval (Inspect samples/@task, OpenAI evals, skill-creator, promptfoo) "
             "as questions with grading checks")
    p.add_argument("path", help="Eval file (format detected as by `tasks inspect`)")
    p.add_argument("--format", default=None, help="Force a format name (default: detected)")
    p.add_argument("--scorer", default=None, help="Inspect samples: scorer to translate (match, choice, ...)")
    p.add_argument("--task", default=None, help="Inspect @task file: which task function")
    p.add_argument("--grader", default=None,
                   help="OpenAI evals: registry YAML, or match|includes|fuzzy_match|json_match|modelgraded:<spec>")
    p.add_argument("-o", "--output", required=True, help="questions.v2 JSON to write")
    p.set_defaults(func=cmd_questions_import)


def register_eval(eval_sub, positive_int) -> None:
    from dokimi.defaults import DEFAULT_JUDGE_PROVIDER, JUDGE_MODEL_FOR_PROVIDER, JUDGE_MODEL_HELP
    from dokimi.eval.judge import JUDGE_PROVIDERS

    r = eval_sub.add_parser("reference", help="Score answers against imported grading checks "
                                              "(match/exact/choice/... deterministic, judge checks via the judge)")
    r.add_argument("--answers", required=True, help="answers.v1 or arms output JSON")
    r.add_argument("--questions", required=True, help="Questions JSON from `questions import`")
    r.add_argument("-o", "--output", required=True, help="eval.v1 (answers) or arms (arms run) JSON to write")
    r.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    r.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    r.add_argument("--no-judge", action="store_true", help="Leave judge checks unscored (no model calls)")
    r.add_argument("--concurrency", type=positive_int, default=4)
    r.set_defaults(func=cmd_eval_reference)

    n = eval_sub.add_parser("native-import", help="Read an `inspect eval --log-format json` log or "
                                                  "`promptfoo eval -o out.json` output as answers + eval")
    n.add_argument("log", help="Native log JSON")
    n.add_argument("--answers-out", required=True, help="answers.v1 JSON to write (completions)")
    n.add_argument("--eval-out", default=None, help="eval.v1 JSON to write (the native verdicts)")
    n.set_defaults(func=cmd_eval_native_import)
