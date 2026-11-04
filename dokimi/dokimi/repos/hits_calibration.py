# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A person's labels against the judge's for ``repo hits``: the sheet to label, and agreement and kappa.

``repo hits sheet`` samples credited pairs for a person to label, stratified by arm and answer model (the rows
of the judged table): ``per_arm`` pairs of each (arm, model) (all of them when it has fewer), drawn with
``random.Random(seed)``, so one seed gives one sheet. ``repo hits judge --labels`` reads the sheet back with
``label`` filled in and reports, per (arm, model), the pairs both labelled, the share on which they agree, and
Cohen's kappa over the three labels.
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
    """The pairs to label, ``per_arm`` per (arm, model) in that order, each in the order ``repo report --hits``
    lists it: each row of the judged table gets its own sample, so each can be calibrated.

    A pair whose full text was not found cannot be labelled and is left out."""
    rng = random.Random(seed)
    by_row: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, pair in enumerate(pairs):
        if pair.problem is None:
            by_row[pair.row].append(i)
    chosen = []
    for row in sorted(by_row):
        indices = by_row[row]
        chosen += sorted(indices if len(indices) <= per_arm else rng.sample(indices, per_arm))
    return [_entry(pairs[i]) for i in chosen]


def _entry(pair: Pair) -> dict[str, Any]:
    hit = pair.hit
    return {"cell": hit["cell"], "task": hit["task"], "arm": hit["arm"], "model": hit["model"], "rep": hit["rep"],
            "thread": hit.get("thread"), "thread_file": hit.get("thread_file"), "thread_line": hit.get("thread_line"),
            "finding": hit.get("finding"), "file": hit.get("file"), "line": hit.get("line"),
            "comment": pair.comment, "finding_text": pair.text, "label": None}


def labelled_entries(path: Path) -> int:
    """The entries of an existing sheet at ``path`` whose ``label`` is set; 0 when there is no file, it is blank,
    or it is JSON but not a list (``repo hits sheet`` replaces such a file, and refuses to replace one with labels).

    Raises:
        HitsInputError: the file is there but cannot be read (no permission, a directory, not UTF-8 text) or
            parsed as JSON (a hand-edit slip, a BOM), so whether it holds labels is not known.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise _unreadable(path, exc.strerror or str(exc)) from exc
    except UnicodeDecodeError as exc:
        raise _unreadable(path, f"not UTF-8 text: {exc}") from exc
    if not text.strip():
        return 0
    try:
        entries = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise _unreadable(path, f"not JSON: {exc}") from exc
    return sum(isinstance(e, dict) and e.get("label") is not None for e in entries) if isinstance(entries, list) else 0


def _unreadable(path: Path, why: str) -> HitsInputError:
    return HitsInputError(f"cannot read {path} to check it for labels ({why}); move it aside or pass another --out")


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
            regrade), or has a label other than the three; a labelled entry has no ``comment`` or no
            ``finding_text``, or either is not, exactly, the pair's text now (the thread's full comment in the
            gold file, the finding's full text in ``answer.txt``), or the pair's texts are not found now; or two
            entries give one pair two different labels (the same label twice, or a null next to a label, is
            taken).
    """
    known = {p.key: p for p in pairs}
    cells = {p.cell for p in pairs}
    out: dict[tuple[str, str], str] = {}
    first: dict[tuple[str, str], int] = {}  # the entry each label of ``out`` came from
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
        _check_texts(where, entry, known[key])
        if key in out and out[key] != label:
            raise HitsInputError(
                f"{path} entries {first[key]} and {i} label thread {entry['thread']!r} of cell {entry['cell']} "
                f"twice, {out[key]!r} and {label!r}: keep one label per pair")
        out[key] = label
        first.setdefault(key, i)
    return out


def _check_texts(where: str, entry: dict[str, Any], pair: Pair) -> None:
    """A labelled entry's ``comment`` and ``finding_text`` are, character for character, the pair's texts now."""
    if "comment" not in entry or "finding_text" not in entry:
        raise HitsInputError(f"{where} is labelled but has no `comment` or no `finding_text` to check the label "
                             "against; `repo hits sheet` writes both")
    named = f"thread {entry['thread']!r} of cell {entry['cell']}"
    if pair.problem is not None:
        raise HitsInputError(f"{where}: the full texts of {named} are not found now ({pair.problem}), so the label "
                             "cannot be checked against them; write a new sheet with `repo hits sheet`")
    changed = [what for what, key, now in (("the thread's comment", "comment", pair.comment),
                                           ("the finding's text", "finding_text", pair.text))
               if not isinstance(entry[key], str) or entry[key] != now]
    if changed:
        raise HitsInputError(
            f"{where}: {' and '.join(changed)} of {named} is not the text the sheet labelled (a sheet older than a "
            "change to answer.txt or the gold file, or a hand-edit); write a new sheet with `repo hits sheet`")


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
    """Per (arm, model), a row of the judged table: ``labelled`` pairs both labelled, ``agreement`` (the share
    with equal labels) and ``kappa``. One answer model's labels say nothing of the judge on another's findings.

    A labelled pair the judge left unjudged is not counted."""
    by_row: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for pair in pairs:
        if pair.key in person and pair.key in judge:
            by_row[pair.row].append((person[pair.key], judge[pair.key]))
    out = []
    for arm, model in sorted({p.row for p in pairs if p.key in person}):
        both = by_row.get((arm, model), [])
        a, b = [x for x, _ in both], [y for _, y in both]
        out.append({"arm": arm, "model": model, "labelled": len(both),
                    "agreement": round(sum(x == y for x, y in both) / len(both), 3) if both else None,
                    "kappa": cohen_kappa(a, b)})
    return out
