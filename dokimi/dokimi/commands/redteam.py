# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval redteam``: check that a question set's grading rejects the answers it should.

Reads a questions set (with ``grading`` blocks), grades synthetic attacks against it and prints
the pass rates. Exits 1 when any attack is accepted, a canary is rejected, a graded question was not
measured, or nothing was graded (``inconclusive``); exits 2 on an unreadable input, a malformed
``--judge`` or an unwritable ``--out``. Judge checks are not scored unless ``--judge PROVIDER/MODEL``
is given, so the default run makes no model call.
"""

from __future__ import annotations

import argparse
import sys


def _ask(args: argparse.Namespace):
    if not args.judge:
        return None
    from dokimi.eval.judge import judge_ask

    provider, model = args.judge
    return judge_ask(model, provider)


def _judge_ref(value: str) -> tuple[str, str]:
    from dokimi.eval.judge import JUDGE_PROVIDERS

    provider, _, model = value.partition("/")
    if provider not in JUDGE_PROVIDERS or not model.strip():
        raise argparse.ArgumentTypeError(
            f"expected PROVIDER/MODEL with PROVIDER one of {', '.join(JUDGE_PROVIDERS)}, got {value!r}")
    return provider, model.strip()


def cmd_eval_redteam(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.eval.redteam import format_table, run
    from dokimi.questions.artifact import load_questions

    try:
        questions = load_questions(args.questions).artifact.get("questions") or []
        result = run(questions, ask=_ask(args))
    except (OSError, ValueError, RecursionError) as e:  # RecursionError: nested too deep
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    result["questions_path"] = str(args.questions)
    result["judge_model"] = args.judge[1] if args.judge else None
    result["judge_provider"] = args.judge[0] if args.judge else None
    print(format_table(result, output=args.out))
    if args.out:
        try:
            save_artifact("redteam", result, args.out)
        except (OSError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Wrote redteam artifact to {args.out}")
    if result["status"] != "pass":
        sys.exit(1)


def register_eval(eval_sub) -> None:
    r = eval_sub.add_parser(
        "redteam",
        help="Check grading blocks reject wrong, empty and hedged answers and accept a canary")
    r.add_argument("questions", help="questions JSON with grading blocks (from `questions import`)")
    r.add_argument("--judge", metavar="PROVIDER/MODEL", type=_judge_ref, default=None,
                   help="Grade judge checks with this judge model (model calls); default leaves them unscored")
    r.add_argument("--out", metavar="FILE", default=None, help="redteam.v1 JSON to write")
    r.set_defaults(func=cmd_eval_redteam)
