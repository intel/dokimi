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

``--verbatim-words N`` (default 12) also flags an item on any run of N words copied from its expected text, whatever
the share of its runs. A skill file that is not UTF-8 text, or is a symbolic link, is not read: the report lists it
as a warning.

``--relock`` overwrites the lock only when the run passes; a blocked or inconclusive run leaves the old lock.

Exits 1 when the run is blocked (a leak, a changed split, a test item given to the proposer) or inconclusive
(an item could not be checked, or none could); exits 2 on an unreadable input, a malformed split, lock or
proposer list, or an unwritable ``--out``. Makes no model call.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dokimi.utils import InputError


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number of words, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number of words, got {value!r}")
    return number


def _fraction(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a number in (0, 1], got {value!r}") from None
    if not 0 < number <= 1:
        raise argparse.ArgumentTypeError(f"expected a number in (0, 1], got {value!r}")
    return number


def _refuse_clobber(args: argparse.Namespace, lock_path: Path | None) -> None:
    """Exit 2 when --out or the lock would replace an input of this run.

    An output over an input replaces it: --out would become the leak.v1 artifact, --lock the split's lock.
    The skill is every file under its directory, since --out can name any of them.
    """
    skill_root = Path(args.skill)
    if skill_root.is_file():
        skill_root = skill_root.parent  # read_skill reads the directory of a SKILL.md argument
    skill_files = [p for p in skill_root.rglob("*") if p.is_file()] if skill_root.is_dir() else []
    inputs = {os.path.realpath(p) for p in [args.skill, args.questions, args.split, args.proposer, args.allow_file,
                                            *skill_files] if p}
    out_real = os.path.realpath(args.out) if args.out else None
    lock_real = os.path.realpath(lock_path) if lock_path is not None else None
    clobbered = [p for p in (out_real, lock_real) if p in inputs]
    if out_real is not None and out_real == lock_real:
        clobbered.append(out_real)  # --out and the lock are one file: the artifact would replace the lock
    if clobbered:
        raise InputError(f"{clobbered[0]} is an input of this run; choose another output file")


def cmd_eval_leak(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.eval.leakgate import format_table, read_ids, read_skill, read_split, read_terms, run
    from dokimi.questions.artifact import load_questions

    if args.lock and not args.split:
        raise InputError("--lock locks a split: give --split too")
    if args.relock and not args.split:
        raise InputError("--relock overwrites the split's lock: give --split too")
    split_path = Path(args.split) if args.split else None
    lock_path = None
    if split_path is not None:
        lock_path = Path(args.lock) if args.lock else split_path.with_name(split_path.stem + ".lock.json")
    _refuse_clobber(args, lock_path)
    skipped: list[str] = []
    try:
        files = read_skill(args.skill, skipped=skipped)
        questions = load_questions(args.questions).artifact.get("questions") or []
        result = run(files, questions, allow=read_terms(args.allow or [], args.allow_file),
                     min_fraction=args.min_fraction, verbatim=args.verbatim_words,
                     assignment=read_split(split_path) if split_path is not None else None,
                     lock_path=lock_path,
                     proposer_ids=read_ids(args.proposer) if args.proposer else None,
                     relock=args.relock)
    except (OSError, ValueError, RecursionError) as e:  # RecursionError: nested too deep
        raise InputError(str(e)) from e
    result["skill_path"] = str(args.skill)
    result["questions_path"] = str(args.questions)
    result["split_path"] = str(split_path) if split_path is not None else None
    result["lock_path"] = str(lock_path) if lock_path is not None else None
    result["warnings"] = skipped
    print(format_table(result, output=args.out))
    if args.out:
        try:
            save_artifact("leak", result, args.out)
        except (OSError, ValueError) as e:
            raise InputError(str(e)) from e
        print(f"Wrote leak artifact to {args.out}")
    if result["status"] != "pass":
        sys.exit(1)


def register_eval(eval_sub) -> None:
    from dokimi.eval.leakgate import DEFAULT_MIN_FRACTION, DEFAULT_VERBATIM

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
    r.add_argument("--verbatim-words", metavar="N", type=_positive_int, default=DEFAULT_VERBATIM,
                   help=f"flag an item on any run of this many words copied from its expected text, "
                        f"whatever the share (default {DEFAULT_VERBATIM})")
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
