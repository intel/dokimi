# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A person's labels against the judge's for ``repo hits``: the sheet to label, and agreement and kappa.

``repo hits sheet`` samples credited pairs for a person to label, stratified by arm: ``per_arm`` pairs of
each arm (all of them when it has fewer), drawn with ``random.Random(seed)``, so one seed gives one sheet.
``repo hits judge --labels`` reads the sheet back with ``label`` filled in and reports, per arm, the
pairs both labelled, the share on which they agree, and Cohen's kappa over the three labels.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence

from .hits_judge import LABELS, HitsInputError, Pair, pair_key

PER_ARM = 25


def sheet(pairs: list[Pair], per_arm: int = PER_ARM, seed: int = 0) -> list[dict[str, Any]]:
    """The pairs to label, ``per_arm`` per arm in arm order, each in the order ``repo report --hits`` lists it.

    A pair whose full text was not found cannot be labelled and is left out."""
    rng = random.Random(seed)
    by_arm: dict[str, list[int]] = defaultdict(list)
    for i, pair in enumerate(pairs):
        if pair.problem is None:
            by_arm[pair.arm].append(i)
    chosen = []
    for arm in sorted(by_arm):
        indices = by_arm[arm]
        chosen += sorted(indices if len(indices) <= per_arm else rng.sample(indices, per_arm))
    return [_entry(pairs[i]) for i in chosen]


def _entry(pair: Pair) -> dict[str, Any]:
    hit = pair.hit
    return {"cell": hit["cell"], "task": hit["task"], "arm": hit["arm"], "model": hit["model"], "rep": hit["rep"],
            "thread": hit.get("thread"), "thread_file": hit.get("thread_file"), "thread_line": hit.get("thread_line"),
            "finding": hit.get("finding"), "file": hit.get("file"), "line": hit.get("line"),
            "comment": pair.comment, "finding_text": pair.text, "label": None}


def _entries(path: Path) -> list[Any]:
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HitsInputError(f"cannot read the labels file {path}: {exc.strerror or exc}") from exc
    except (ValueError, RecursionError) as exc:
        raise HitsInputError(f"the labels file {path} is not valid JSON: {exc}") from exc
    if not isinstance(entries, list):
        raise HitsInputError(f"the labels file {path} must hold a JSON list, as `repo hits sheet` writes it")
    return entries


def read_labels(path: Path, pairs: list[Pair]) -> dict[tuple[str, str], str]:
    """A person's label of each pair, by :func:`.hits_judge.pair_key`; an entry whose ``label`` is null is not
    labelled yet and is skipped.

    Raises:
        HitsInputError: the file is unreadable or not a list, or an entry is not an object with ``cell``,
            ``thread`` and ``finding``, names a cell or a thread of no credited pair of the batch, names another
            finding than the one the batch credits for its thread (a hand-edit, or a sheet older than a
            regrade), or has a label other than the three.
    """
    known = {p.key: p for p in pairs}
    cells = {p.cell for p in pairs}
    out: dict[tuple[str, str], str] = {}
    for i, entry in enumerate(_entries(path)):
        where = f"{path} entry {i}"
        if (not isinstance(entry, dict) or not isinstance(entry.get("cell"), str)
                or "thread" not in entry or "finding" not in entry):
            raise HitsInputError(f"{where} is not an object with `cell`, `thread`, `finding` and `label`")
        if entry["cell"] not in cells:
            raise HitsInputError(f"{where}: cell {entry['cell']!r} has no credited pair in this batch")
        key = pair_key(entry["cell"], entry["thread"])
        if key not in known:
            raise HitsInputError(f"{where}: thread {entry['thread']!r} is not a credited pair of cell {entry['cell']}")
        credited = known[key].hit.get("finding")
        if type(entry["finding"]) is not type(credited) or entry["finding"] != credited:
            raise HitsInputError(
                f"{where}: the sheet labels finding {entry['finding']!r} of thread {entry['thread']!r}, but the "
                f"batch credits finding {credited!r} for it in cell {entry['cell']}; write a new sheet with "
                "`repo hits sheet`")
        label = entry.get("label")
        if label is None:
            continue
        if label not in LABELS:
            raise HitsInputError(f"{where}: label {label!r} is not one of " + ", ".join(repr(x) for x in LABELS))
        out[key] = label
    return out


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    """Cohen's kappa of two raters over :data:`.hits_judge.LABELS`; ``None`` with no pair or when chance
    agreement is 1 (both gave one label to every pair)."""
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    chance = sum((a.count(c) / n) * (b.count(c) / n) for c in LABELS)
    return None if chance >= 1 else round((observed - chance) / (1 - chance), 3)


def calibrate(person: dict[tuple[str, str], str], judge: dict[tuple[str, str], str],
              pairs: list[Pair]) -> list[dict[str, Any]]:
    """Per arm: ``labelled`` pairs both labelled, ``agreement`` (the share with equal labels) and ``kappa``.

    A labelled pair the judge left unjudged is not counted."""
    by_arm: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for pair in pairs:
        if pair.key in person and pair.key in judge:
            by_arm[pair.arm].append((person[pair.key], judge[pair.key]))
    out = []
    for arm in sorted({p.arm for p in pairs if p.key in person}):
        both = by_arm.get(arm, [])
        a, b = [x for x, _ in both], [y for _, y in both]
        out.append({"arm": arm, "labelled": len(both),
                    "agreement": round(sum(x == y for x, y in both) / len(both), 3) if both else None,
                    "kappa": cohen_kappa(a, b)})
    return out
