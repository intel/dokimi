# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval leak``: check a candidate skill for held-out answers, and lock the dev/val/test split.

Reads a skill (a directory, or its SKILL.md) and a questions set, and prints whether any eval item's expected
answer or check appears in the skill, n-gram by n-gram (see :mod:`dokimi.eval.leakgate`). ``--allow`` and
``--allow-file`` list the terms that legitimately appear in both (API names, paths, identifiers).

``--split FILE`` gives the dev/val/test assignment. Its digest is locked in ``--lock`` (default: the split's
name with ``.lock.json``) at first use; a later run whose split or any item's grading block changed is blocked.
``--relock`` overwrites the lock with the current split and reports the digest it replaced. ``--proposer FILE``
(a JSON list of question ids) blocks a run that gives the proposer a test item.

Exits 1 when the run is blocked (a leak, a changed split, a test item given to the proposer) or inconclusive
(an item could not be checked, or none could); exits 2 on an unreadable input, a malformed split, lock or
proposer list, or an unwritable ``--out``. Makes no model call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _fraction(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number in (0, 1], got {value!r}") from None
    if not 0 < number <= 1:
        raise argparse.ArgumentTypeError(f"expected a number in (0, 1], got {value!r}")
    return number


def cmd_eval_leak(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.eval.leakgate import format_table, read_ids, read_skill, read_split, read_terms, run
    from dokimi.questions.artifact import load_questions

    if args.lock and not args.split:
        print("Error: --lock locks a split: give --split too", file=sys.stderr)
        sys.exit(2)
    if args.relock and not args.split:
        print("Error: --relock overwrites the split's lock: give --split too", file=sys.stderr)
        sys.exit(2)
    split_path = Path(args.split) if args.split else None
    lock_path = None
    if split_path is not None:
        lock_path = Path(args.lock) if args.lock else split_path.with_name(split_path.stem + ".lock.json")
    try:
        files = read_skill(args.skill)
        questions = load_questions(args.questions).artifact.get("questions") or []
        result = run(files, questions, allow=read_terms(args.allow or [], args.allow_file),
                     min_fraction=args.min_fraction,
                     assignment=read_split(split_path) if split_path is not None else None,
                     lock_path=lock_path,
                     proposer_ids=read_ids(args.proposer) if args.proposer else None,
                     relock=args.relock)
    except (OSError, ValueError, RecursionError) as e:  # RecursionError: nested too deep
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    result["skill_path"] = str(args.skill)
    result["questions_path"] = str(args.questions)
    result["split_path"] = str(split_path) if split_path is not None else None
    result["lock_path"] = str(lock_path) if lock_path is not None else None
    print(format_table(result, output=args.out))
    if args.out:
        try:
            save_artifact("leak", result, args.out)
        except (OSError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Wrote leak artifact to {args.out}")
    if result["status"] != "pass":
        sys.exit(1)


def register_eval(eval_sub) -> None:
    from dokimi.eval.leakgate import DEFAULT_MIN_FRACTION

    r = eval_sub.add_parser(
        "leak",
        help="Check a skill for held-out answers and lock the dev/val/test split")
    r.add_argument("skill", help="skill directory, or its SKILL.md")
    r.add_argument("questions", help="questions JSON with grading blocks (from `eval import`)")
    r.add_argument("--allow", metavar="TERM", action="append", default=None,
                   help="a term that may appear in both the skill and the answers (repeatable)")
    r.add_argument("--allow-file", metavar="FILE", default=None,
                   help="allowlist, one term per line; blank lines and lines starting with # are skipped")
    r.add_argument("--min-fraction", metavar="F", type=_fraction, default=DEFAULT_MIN_FRACTION,
                   help=f"flag an item when this share of its answer n-grams is in the skill, in (0, 1] "
                        f"(default {DEFAULT_MIN_FRACTION})")
    r.add_argument("--split", metavar="FILE", default=None,
                   help='split JSON {"dev": [ids], "val": [ids], "test": [ids]}; checked against the lock')
    r.add_argument("--lock", metavar="FILE", default=None,
                   help="split lock JSON (default: the split file's name with .lock.json; created at first use)")
    r.add_argument("--relock", action="store_true",
                   help="overwrite the split's lock with this split when it differs; the report keeps the old digest")
    r.add_argument("--proposer", metavar="FILE", default=None,
                   help="JSON list of the question ids the proposer sees; a test id here blocks the run")
    r.add_argument("--out", metavar="FILE", default=None, help="leak.v1 JSON to write")
    r.set_defaults(func=cmd_eval_leak)
