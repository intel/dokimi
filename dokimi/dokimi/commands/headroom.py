# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval headroom``: flag the ceiling, floor and provisional items of arms runs.

Reads one or more ``arms.v1`` files of the same question set (one per repetition) and writes a
``headroom.v1`` artifact with ``--out``, always printing the human table. A file repeated on the
command line, or holding the same JSON content as an earlier one, is one run: the repeats are skipped with a
note on stderr. See :mod:`dokimi.eval.headroom` for the rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from dokimi import records
from dokimi.commands._output import exit_on_unwritable


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


def _names_a_directory(output) -> bool:
    """Whether ``--out`` spells a directory: its last component is empty, ``.`` or ``..`` (a trailing
    separator, with or without ``/.``), or it is an existing directory. A path that cannot be
    stat-ed is not one: the save reports it, as before."""
    text = str(output)
    if text.replace(os.sep, "/").rsplit("/", 1)[-1] in ("", ".", ".."):
        return True
    try:
        return Path(text).is_dir()
    except (OSError, ValueError):
        return False


def _clobbers_an_input(output, inputs) -> bool:
    """Whether a save to ``output`` would overwrite an input: the file itself, or the ``.rejected``
    sibling save_artifact writes on a schema failure and deletes after a good save."""
    out = Path(output)
    rejected = out.with_name(out.name + ".rejected")
    return any(_same_file(out, p) or _same_file(rejected, p) for p in inputs)


def cmd_eval_headroom(args: argparse.Namespace) -> None:
    from dokimi.artifacts import ArtifactValidationError, save_artifact
    from dokimi.eval.headroom import format_table, headroom, repeated_ids

    if args.output is not None and _names_a_directory(args.output):
        # A directory has no file name for save_artifact's sibling paths ("." has an empty name), and
        # Path() drops a trailing slash, so "newdir/" would otherwise write a plain file named newdir.
        print(f"Error: --out {args.output} is not a file name (a directory, or a path ending in a separator)",
              file=sys.stderr)
        sys.exit(2)
    if args.output is not None and _clobbers_an_input(args.output, args.arms):
        print(f"Error: --out {args.output} would overwrite one of the input arms files (or its .rejected sibling); "
              "the run would be lost", file=sys.stderr)
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
    if args.output is not None:
        try:
            with exit_on_unwritable(args.output):  # an unwritable output exits 2, as docs/quickstart.md says
                save_artifact("headroom", result, args.output)
        except ArtifactValidationError as e:  # a payload the schema refuses is an internal failure: exit 1
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Wrote headroom artifact to {args.output}")


def register_eval(eval_sub, positive_int) -> None:
    from dokimi.eval.headroom import DEFAULT_CEILING, DEFAULT_FLOOR, DEFAULT_MIN_REPS

    h = eval_sub.add_parser(
        "headroom",
        help="Flag arms items with no headroom: no-skill ceiling, all-zero floor, provisional reps")
    h.add_argument("arms", nargs="+", help="arms.v1 JSON, one per repetition of the question set")
    h.add_argument("--baseline-arm", default=None, dest="no_skill_arm",
                   help="Arm read as the no-skill control (default: the baseline the runs declare)")
    h.add_argument("--ceiling", type=float, default=DEFAULT_CEILING,
                   help=f"Flag an item when the no-skill arm scores at least this, 0-1 (default {DEFAULT_CEILING})")
    h.add_argument("--floor", type=float, default=DEFAULT_FLOOR,
                   help=f"Flag an item when every arm scores at most this, 0-1 (default {DEFAULT_FLOOR})")
    h.add_argument("--min-reps", type=positive_int, default=DEFAULT_MIN_REPS, dest="min_reps",
                   help=f"Items with fewer runs than this are provisional (default {DEFAULT_MIN_REPS})")
    h.add_argument("--out", dest="output", default=None, help="headroom.v1 JSON to write")
    h.set_defaults(func=cmd_eval_headroom)
