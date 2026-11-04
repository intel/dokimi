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
from typing import Any

from dokimi import records
from dokimi.commands._output import exit_on_unwritable
from dokimi.eval.judge_context import PROTOCOL_MIXED


def _read_json(path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))  # records.load has parsed it already


def _canonical(value: Any) -> Any:
    """``value`` with each integral float written as the int it equals (95.0 becomes 95). Booleans and
    strings are left as they are, so ``true`` and ``"95"`` never equal a number."""
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _content_digest(data: Any) -> str:
    """Digest of the parsed JSON, so one run re-serialized (whitespace, key order, 95 vs 95.0) counts once."""
    return hashlib.sha256(json.dumps(_canonical(data), sort_keys=True).encode("utf-8")).hexdigest()


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


def _exit_unless_replicates(runs, inputs) -> None:
    """Exit 2 on the first input that is not a repetition of the first one's configuration (models,
    judge, run_config but the seed, harness, plugins, cell, arms: report.model_compare.replicate_key)
    or library: averaging such runs per question id would mix different measurements. A run that lists no
    arms at the top level (absent or empty, as replicate_key reads it) is compared by the arms its records show."""
    from dokimi.report.model_compare import replicate_key

    keys = [(replicate_key(dict(run.meta)), run.meta.get("library_name"), run.meta.get("judge_context"),
             None if run.meta.get("arms") else tuple(sorted(run.conditions)))
            for run in runs]
    odd = next((path for key, path in zip(keys[1:], inputs[1:], strict=True) if key != keys[0]), None)
    if odd is not None:
        print(f"Error: {odd} is not a repetition of {inputs[0]}: the model, judge, judge context, run config, "
              "harness, arms or library differ, and runs of different configurations are not pooled", file=sys.stderr)
        sys.exit(2)


# The per-run tallies of a reference scoring: they count this run's answers, so repetitions differ in them.
_RUN_TALLIES = ("summary", "ungraded_questions")


def _grading_identity(meta) -> Any:
    """What a reference scoring graded against (its scorer and questions file, say), without the tallies."""
    scoring = meta.get("reference_scoring")
    if isinstance(scoring, dict):
        scoring = {key: value for key, value in scoring.items() if key not in _RUN_TALLIES}
    return _canonical(scoring)


def _exit_on_mixed_scoring(runs, inputs) -> None:
    """Exit 2 when some inputs were scored by ``eval reference`` and others by the judge, or two reference
    scorings graded differently. A rescored file keeps the judge's metadata, so it passes the replicate check,
    yet its scores are a different measurement."""
    kinds = ["reference" if "reference_scoring" in run.meta else "judge" for run in runs]
    odd = next((i for i, kind in enumerate(kinds) if kind != kinds[0]), None)
    if odd is not None:
        print(f"Error: {inputs[odd]} was scored by the {kinds[odd]} and {inputs[0]} by the {kinds[0]}: "
              "the two scorings are different measurements, and are not pooled", file=sys.stderr)
        sys.exit(2)
    references = [i for i, kind in enumerate(kinds) if kind == "reference"]
    if references:
        first = references[0]
        odd = next((i for i in references[1:] if _grading_identity(runs[i].meta) != _grading_identity(runs[first].meta)),
                   None)
        if odd is not None:
            print(f"Error: {inputs[odd]} was scored by eval reference against different grading than {inputs[first]} "
                  "(its scorer or questions file differ): the two scorings are different measurements, and are not pooled",
                  file=sys.stderr)
            sys.exit(2)


def _exit_on_mixed_grounding(runs, inputs) -> None:
    """Exit 2 when one question id is grounded (judged against its ground truth chunk) in one input and not in
    another: the judge uses different prompts and weights for the two. A run states its grounding in
    ``grounded_questions``; a run without that count (a legacy file) has unknown grounding and is not compared."""
    where = {True: "grounded", False: "ungrounded"}
    seen: dict[str, tuple[bool, str]] = {}  # question id -> (grounded, first input)
    for run, path in zip(runs, inputs, strict=True):
        if "grounded_questions" not in run.meta:
            continue
        for rec in run:
            grounded = bool(rec.ground_truth)
            first = seen.setdefault(rec.key, (grounded, path))
            if first[0] != grounded:
                print(f"Error: question_id {rec.key!r} is {where[grounded]} in {path} but {where[first[0]]} in "
                      f"{first[1]}: the judge scores the two differently, so the runs are not pooled", file=sys.stderr)
                sys.exit(2)


def _prompt_pins(data: Any, run) -> dict[str, set[str]]:
    """Per arm of ``run``, the profile prompt digests its records stamp (none for an arm without a profile)."""
    from dokimi.report.model_compare import prompt_digests

    return {arm: prompt_digests(data, arm) for arm in run.conditions}


def _exit_on_conflicting_prompts(pins, inputs) -> None:
    """Exit 2 when an arm's recorded profile prompt digest differs between inputs, or within one. An edited profile
    keeps its frontmatter id, so the arm name alone would pool two different treatments and hide a floor or ceiling."""
    seen: dict[str, tuple[str, str]] = {}  # arm -> (digest, first input)
    for pinned, path in zip(pins, inputs, strict=True):
        for arm, digests in pinned.items():
            for digest in sorted(digests):
                first = seen.setdefault(arm, (digest, path))
                if first[0] != digest:
                    print(f"Error: arm {arm!r} ran with a different profile prompt in {path} than in {first[1]} "
                          f"(profile_prompt_sha256 {digest[:12]} vs {first[0][:12]}): the profile was edited, "
                          "and the two treatments are not pooled", file=sys.stderr)
                    sys.exit(2)


def _exit_on_conflicting_questions(runs, inputs) -> None:
    """Exit 2 when two inputs give one question id a different question text or ground truth (a
    non-empty value each): pooled under one id, the two questions would hide each other's headroom.
    Partial runs may still differ in their id sets; only a shared id is compared."""
    seen: dict[str, dict[str, tuple[str, str]]] = {}  # question id -> field -> (value, first input)
    for run, path in zip(runs, inputs, strict=True):
        for rec in run:
            for name, value in (("question text", rec.question_text), ("ground truth", rec.ground_truth)):
                if not value:
                    continue
                first = seen.setdefault(rec.key, {}).setdefault(name, (value, path))
                if first[0] != value:
                    print(f"Error: question_id {rec.key!r} has a different {name} in {path} than in "
                          f"{first[1]}: the two are different questions and are not pooled",
                          file=sys.stderr)
                    sys.exit(2)


# replicate_key calls .items() on run_config and tuple() on arms: a wrong type there crashes it, or (an
# arms string or object) is silently split into characters or keys. Both are refused here with a diagnostic.
_METADATA_SHAPES = (("run_config", dict, "object"), ("arms", list, "array"))


def _exit_unless_pinnable(data, run, path) -> None:
    """Exit 2 when the profile-prompt check cannot read the file: its top level is not an object (a bare record
    list has none of the arms.v1 fields), or an answer's metadata, or its treatments, is not the shape it reads."""
    if not isinstance(data, dict):
        print(f"Error: {path}: expected a JSON object with the arms.v1 fields, not a {type(data).__name__}",
              file=sys.stderr)
        sys.exit(2)
    for rec in run:
        for answer in rec.answers.values():
            metadata = answer.raw.get("metadata")
            if not metadata:
                continue
            if not isinstance(metadata, dict):
                print(f"Error: {path}: answer metadata must be a JSON object, not {type(metadata).__name__}",
                      file=sys.stderr)
                sys.exit(2)
            treatments = metadata.get("treatments")
            if treatments and not isinstance(treatments, list):
                print(f"Error: {path}: metadata.treatments must be a JSON array, not {type(treatments).__name__}",
                      file=sys.stderr)
                sys.exit(2)


def _exit_unless_metadata_shaped(run, path) -> None:
    """Exit 2 when a top-level field replicate_key reads is not the JSON shape it needs (absent or null
    is fine), or the run mixed judge protocols, naming the file and the field."""
    for field, shape, label in _METADATA_SHAPES:
        value = run.meta.get(field)
        if value is not None and not isinstance(value, shape):
            print(f"Error: {path}: {field} must be a JSON {label}, not {type(value).__name__}",
                  file=sys.stderr)
            sys.exit(2)
    if run.meta.get("arms") is not None and not all(isinstance(arm, str) for arm in run.meta["arms"]):
        print(f"Error: {path}: arms must be a JSON array of strings", file=sys.stderr)
        sys.exit(2)
    if run.meta.get("judge_context") == PROTOCOL_MIXED:
        print(f"Error: {path}: judged some answers on the stored treatment material and some on report "
              f"snippets (judge_context {PROTOCOL_MIXED!r}); such a run pools with nothing", file=sys.stderr)
        sys.exit(2)


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
    pins: list[dict[str, set[str]]] = []
    first_seen: dict[str, str] = {}
    for path in args.arms:
        try:
            run = records.load(path)
            data = _read_json(path)
            digest = _content_digest(data)
        except (records.RecordError, OSError, RecursionError) as e:  # RecursionError: nesting too deep for json
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(2)
        _exit_unless_metadata_shaped(run, path)
        _exit_unless_pinnable(data, run, path)
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
        pins.append(_prompt_pins(data, run))
    _exit_unless_replicates(runs, inputs)
    _exit_on_mixed_scoring(runs, inputs)
    _exit_on_mixed_grounding(runs, inputs)
    _exit_on_conflicting_prompts(pins, inputs)
    _exit_on_conflicting_questions(runs, inputs)
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
