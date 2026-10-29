# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval redteam``: check that a question set's grading rejects the answers it should.

Reads a questions set (with ``grading`` blocks), grades synthetic attacks against it and prints
the pass rates. Exits 1 when any attack is accepted, a canary is rejected, or nothing was graded
(``inconclusive``); exits 2 on an unreadable input or an unwritable ``-o``. Judge checks are not
scored unless ``--judge`` is given, so the default run makes no model call.
"""

from __future__ import annotations

import argparse
import sys


def _ask(args: argparse.Namespace):
    if not args.judge:
        return None
    from dokimi.eval.judge import judge_ask

    return judge_ask(args.judge_model, args.judge_provider)


def cmd_eval_redteam(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.eval.redteam import format_table, run
    from dokimi.questions.artifact import load_questions

    try:
        questions = load_questions(args.questions).artifact.get("questions") or []
        result = run(questions, ask=_ask(args))
    except (OSError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    result["questions_path"] = str(args.questions)
    result["judge_model"] = args.judge_model if args.judge else None
    result["judge_provider"] = args.judge_provider if args.judge else None
    print(format_table(result, output=args.output))
    if args.output:
        try:
            save_artifact("redteam", result, args.output)
        except (OSError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Wrote redteam artifact to {args.output}")
    if result["status"] != "pass":
        sys.exit(1)


def register_eval(eval_sub) -> None:
    from dokimi.defaults import DEFAULT_JUDGE_PROVIDER, JUDGE_MODEL_FOR_PROVIDER, JUDGE_MODEL_HELP
    from dokimi.eval.judge import JUDGE_PROVIDERS

    r = eval_sub.add_parser(
        "redteam",
        help="Check grading blocks reject wrong, empty, hedged and stuffed answers and accept a canary")
    r.add_argument("questions", help="questions JSON with grading blocks (from `questions import`)")
    r.add_argument("--judge", action="store_true",
                   help="Grade judge checks with the judge model (model calls); default leaves them unscored")
    r.add_argument("--judge-model", default=JUDGE_MODEL_FOR_PROVIDER, help=JUDGE_MODEL_HELP)
    r.add_argument("--judge-provider", default=DEFAULT_JUDGE_PROVIDER, choices=JUDGE_PROVIDERS)
    r.add_argument("-o", "--output", default=None, help="redteam.v1 JSON to write")
    r.set_defaults(func=cmd_eval_redteam)
