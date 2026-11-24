# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Labels a user attaches to analysed atoms, and the known/new shares per label.

A label file has one row per atom: ``skill`` (the artifact ref; its ``skill:`` prefix is optional),
``atom_id`` and ``label``. It is JSON lines, or CSV with a header when the name ends in ``.csv``.
The labels are joined to the per-atom verdicts of ``analyze`` (``<stem>.atom_verdicts.json``) when
``skill novelty view --atom-labels`` reads them. Nothing is written back: the artifacts do not
change.
"""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from dokimi.utils import InputError

FIELDS = ("skill", "atom_id", "label")
UNLABELLED = "unlabelled"
RESERVED = frozenset({"all", UNLABELLED})  # the view's own bucket names


def skill_name(ref: str) -> str:
    """The skill part of an artifact ref: ``skill:skills/x`` and ``skills/x`` name one skill."""
    return ref.split(":", 1)[1] if ":" in ref else ref


def _rows(path: Path) -> list[Any]:
    try:
        # utf-8-sig: a CSV saved as "CSV UTF-8" by Excel starts with a BOM
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read {path}: {exc}") from exc
    # Split on \n only (the CSV reader and JSON both allow U+2028 and friends inside a value).
    if path.suffix.lower() == ".csv":
        try:
            return list(csv.DictReader(io.StringIO(text, newline="")))
        except csv.Error as exc:  # a field past csv.field_size_limit(), say
            raise InputError(f"{path}: not a readable CSV ({exc})") from exc
    rows = []
    for number, line in enumerate(text.split("\n"), 1):
        if line.strip():
            try:
                row = json.loads(line)
            except (ValueError, RecursionError) as exc:
                raise InputError(f"{path}: line {number} is not JSON ({exc})") from exc
            if not isinstance(row, dict):
                raise InputError(f"{path}: line {number} is not a JSON object")
            rows.append(row)
    return rows


def _text(value: Any) -> str:
    """A field as text. A string or a number is kept (label 0 is a label); a boolean, a list or an
    object is no value, so it reads as a missing field."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return ""
    return str(value).strip()


def load_atom_labels(path: str | Path) -> dict[tuple[str, str], str]:
    """``{(skill name, atom id): label}``. A file with no rows, a row missing a field, a label the
    view reserves for its own buckets, or an atom with two labels is refused."""
    path = Path(path)
    rows = _rows(path)
    if not rows:
        raise InputError(f"{path}: no label rows")
    labels: dict[tuple[str, str], str] = {}
    for number, row in enumerate(rows, 1):
        values = [_text(row.get(f)) for f in FIELDS]
        if not all(values):
            raise InputError(f"{path}: row {number} needs non-empty {', '.join(FIELDS)}")
        if values[2] in RESERVED:
            raise InputError(f"{path}: row {number}: label {values[2]!r} is reserved for the view's own "
                             f"buckets ({', '.join(sorted(RESERVED))}); use another label")
        key = (skill_name(values[0]), values[1])
        if labels.get(key, values[2]) != values[2]:
            raise InputError(f"{path}: atom {key[1]} of {key[0]} has two labels "
                             f"({labels[key]!r}, {values[2]!r})")
        labels[key] = values[2]
    return labels


def unmatched_note(verdict_rows: Iterable[dict], artifact_ref: str,
                   labels: dict[tuple[str, str], str]) -> str | None:
    """A warning when some labels name no atom of the run, else None: a wrong ``skill`` or
    ``atom_id`` would otherwise only read as atoms nobody labelled."""
    skill = skill_name(artifact_ref)
    seen = {(skill, str(row["id"])) for row in verdict_rows}
    missed = sum(key not in seen for key in labels)
    if not missed:
        return None
    return (f"{missed} of {len(labels)} labels match no atom of {artifact_ref}: check their skill "
            f"and atom_id; their atoms are counted as {UNLABELLED}")


def _finish(bucket: dict) -> dict:
    atoms = bucket["atoms"]
    return {**bucket,
            "known_share": round(bucket["known"] / atoms, 3) if atoms else None,
            "new_share": round(bucket["new"] / atoms, 3) if atoms else None}


def label_shares(verdict_rows: Iterable[dict], artifact_ref: str,
                 labels: dict[tuple[str, str], str]) -> dict:
    """Known and new shares overall and per label.

    ``known`` is a category starting ``known_`` (the model already knew it), ``new`` one starting
    ``novel_``; ``other`` is the rest (``scaffolding``, ``harmful_conflicting``). Shares are over
    the atoms that have a category; an atom with none is counted as ``uncategorised`` and in no
    share.
    """
    skill = skill_name(artifact_ref)
    buckets: dict[str, dict] = defaultdict(lambda: {"atoms": 0, "known": 0, "new": 0, "other": 0,
                                                    "uncategorised": 0})
    total = {"atoms": 0, "known": 0, "new": 0, "other": 0, "uncategorised": 0}
    for row in verdict_rows:
        label = labels.get((skill, str(row["id"])), UNLABELLED)
        category = row.get("category")
        for bucket in (buckets[label], total):
            if category is None:
                bucket["uncategorised"] += 1
                continue
            bucket["atoms"] += 1
            kind = ("known" if category.startswith("known_")
                    else "new" if category.startswith("novel_") else "other")
            bucket[kind] += 1
    return {"artifact_ref": artifact_ref,
            "by_label": {name: _finish(buckets[name]) for name in sorted(buckets)},
            "all": _finish(total)}


def format_shares(shares: dict) -> str:
    """A plain table: one row per label, then the whole run."""
    head = (f"{'label':<20} {'atoms':>6} {'known':>6} {'new':>6} {'other':>6} "
            f"{'uncat.':>6} {'known%':>7} {'new%':>7}")
    lines = [f"known/new shares for {shares['artifact_ref']} "
             "(over atoms with a category; uncat. have none)", head]

    def row(name: str, b: dict) -> str:
        pct = lambda v: "-" if v is None else f"{100 * v:.1f}"  # noqa: E731
        return (f"{name:<20} {b['atoms']:>6} {b['known']:>6} {b['new']:>6} {b['other']:>6} "
                f"{b['uncategorised']:>6} {pct(b['known_share']):>7} {pct(b['new_share']):>7}")

    lines += [row(name, b) for name, b in shares["by_label"].items()]
    lines.append(row("all", shares["all"]))
    return "\n".join(lines)
