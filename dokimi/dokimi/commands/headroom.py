# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval headroom``: flag the ceiling, floor and provisional items of arms runs.

Reads one or more ``arms.v1`` files of the same question set (one per repetition) and writes a
``headroom.v1`` artifact with ``-o``, always printing the human table. A file repeated on the
command line, or byte-identical to an earlier one, is one run: the repeats are skipped with a
note on stderr. See :mod:`dokimi.eval.headroom` for the rules.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from dokimi import records


def cmd_eval_headroom(args: argparse.Namespace) -> None:
    from dokimi.artifacts import save_artifact
    from dokimi.eval.headroom import format_table, headroom, repeated_ids

    runs: list[records.Run] = []
    inputs: list[str] = []
    first_seen: dict[str, str] = {}
    for path in args.arms:
        try:
            run = records.load(path)
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except (records.RecordError, OSError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        if digest in first_seen:
            print(f"note: skipped {path}: same content as {first_seen[digest]}, counted once", file=sys.stderr)
            continue
        dupes = repeated_ids(run)
        if dupes:
            print(f"Error: {path}: question_id {dupes[0]!r} appears more than once; "
                  "a file holds each question once", file=sys.stderr)
            sys.exit(2)
        first_seen[digest] = str(path)
        runs.append(run)
        inputs.append(str(path))
    try:
        result = headroom(runs, no_skill_arm=args.no_skill_arm, ceiling=args.ceiling,
                          floor=args.floor, min_reps=args.min_reps)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)
    result["inputs"] = inputs
    print(format_table(result))
    if args.output:
        save_artifact("headroom", result, args.output)
        print(f"Wrote headroom artifact to {args.output}")


def register_eval(eval_sub) -> None:
    from dokimi.eval.headroom import DEFAULT_CEILING, DEFAULT_FLOOR, DEFAULT_MIN_REPS

    h = eval_sub.add_parser(
        "headroom",
        help="Flag arms items with no headroom: no-skill ceiling, all-zero floor, provisional reps")
    h.add_argument("arms", nargs="+", help="arms.v1 JSON, one per repetition of the question set")
    h.add_argument("--no-skill-arm", default=None,
                   help="Arm read as the no-skill control (default: the baseline the runs declare)")
    h.add_argument("--ceiling", type=float, default=DEFAULT_CEILING,
                   help=f"Flag an item when the no-skill arm scores at least this, 0-1 (default {DEFAULT_CEILING})")
    h.add_argument("--floor", type=float, default=DEFAULT_FLOOR,
                   help=f"Flag an item when every arm scores at most this, 0-1 (default {DEFAULT_FLOOR})")
    h.add_argument("--min-reps", type=int, default=DEFAULT_MIN_REPS, dest="min_reps",
                   help=f"Items with fewer runs than this are provisional (default {DEFAULT_MIN_REPS})")
    h.add_argument("-o", "--output", default=None, help="headroom.v1 JSON to write")
    h.set_defaults(func=cmd_eval_headroom)
