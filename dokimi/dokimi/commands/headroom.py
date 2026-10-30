# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval headroom``: flag the ceiling, floor and provisional items of arms runs.

Reads one or more ``arms.v1`` files of the same question set (one per repetition) and writes a
``headroom.v1`` artifact with ``-o``, always printing the human table. A file repeated on the
command line, or holding the same JSON content as an earlier one, is one run: the repeats are skipped with a
note on stderr. See :mod:`dokimi.eval.headroom` for the rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from dokimi import records


def _content_digest(path) -> str:
    """Digest of the parsed JSON, so one run re-serialized (whitespace, key order) counts once."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))  # records.load has parsed it already
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def _same_file(a, b) -> bool:
    """Whether two paths name one file. A path that cannot be resolved (a symlink loop on some Pythons)
    is not taken as an input: its own open reports the error."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, RuntimeError):
        return False


def cmd_eval_headroom(args: argparse.Namespace) -> None:
    from dokimi.artifacts import ArtifactValidationError, save_artifact
    from dokimi.eval.headroom import format_table, headroom, repeated_ids

    if args.output and any(_same_file(args.output, p) for p in args.arms):
        print(f"Error: -o {args.output} is one of the input arms files; it would overwrite that run", file=sys.stderr)
        sys.exit(2)
    runs: list[records.Run] = []
    inputs: list[str] = []
    first_seen: dict[str, str] = {}
    for path in args.arms:
        try:
            run = records.load(path)
            digest = _content_digest(path)
        except (records.RecordError, OSError, RecursionError) as e:  # RecursionError: nesting too deep for json
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
        try:
            save_artifact("headroom", result, args.output)
        except (OSError, ArtifactValidationError) as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        print(f"Wrote headroom artifact to {args.output}")


def register_eval(eval_sub, positive_int) -> None:
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
    h.add_argument("--min-reps", type=positive_int, default=DEFAULT_MIN_REPS, dest="min_reps",
                   help=f"Items with fewer runs than this are provisional (default {DEFAULT_MIN_REPS})")
    h.add_argument("-o", "--output", default=None, help="headroom.v1 JSON to write")
    h.set_defaults(func=cmd_eval_headroom)
