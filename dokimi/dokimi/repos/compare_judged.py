# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo compare --metric judge:recall_judged``: judged recall from each cell's ``hits_judged.json``.

``repo hits judge`` (:mod:`.hits_judge`) writes ``<cell>/hits_judged.json`` beside the grade and leaves
``grade.json`` alone, so a ``--metric`` named ``judge:<field>`` is read from that file, not from ``grade.json``
``metrics``. ``recall_judged`` is the one field it takes (:data:`FIELDS`); another ``judge:`` name is refused.

A cell reports ``judge:recall_judged`` only when its file is a ``repo_hits_judged.v1`` JSON object whose ``cell``,
``task``, ``arm`` and ``model`` are the cell's own (a copied or misplaced file is not this cell's), whose ``recall``
equals the cell's ``grade.json`` ``metrics.recall``, ``n_threads`` and credited ``(thread, finding)`` pairs now
(else it was regraded since judging: the file is stale, even when the recall is the same), and whose ``recall_judged`` is a finite number. Any other cell does not report it, as a cell lacking a ``grade.json``
metric does: it is left out of its task's mean and ``n``. A null ``recall_judged`` (an unjudged pair) is absent,
never 0. Each cell gets one of :data:`REASONS`, and the summary counts them per compared arm (``judged_cells``).

The file carries no calibration, so compare cannot tell whether a person's labels backed the judge: the text says
the figure is uncalibrated unless ``repo hits judge --labels`` reported agreement for these arms.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dokimi.artifacts import SCHEMA_VERSIONS

from .compare_tasks import CompareError
from .hits_judge import HITS_JUDGED
from .process_counts import as_measure

PREFIX = "judge:"
#: The ``hits_judged.json`` fields a ``judge:`` metric may name.
FIELDS = ("recall_judged",)
#: Why a cell does or does not report a ``judge:`` metric, in the order the text names them.
REASONS = ("read", "no_file", "stale", "other_cell", "unjudged", "unreadable")
_SAID = {"no_file": f"no {HITS_JUDGED}", "stale": "stale: regraded since judging",
         "other_cell": "not this cell's (cell, task, arm or model differ)", "unjudged": "with an unjudged pair",
         "unreadable": f"unreadable or not {SCHEMA_VERSIONS['repo_hits_judged']}"}
HINT = "run `repo hits judge <batch> --judge PROVIDER/MODEL`"


def judge_names(metrics: tuple[str, ...]) -> list[str]:
    """The ``judge:`` metrics asked for; any field but :data:`FIELDS` is refused (exit 2)."""
    names = [m for m in metrics if m.startswith(PREFIX)]
    bad = [m for m in names if m[len(PREFIX):] not in FIELDS]
    if bad:
        raise CompareError(f"--metric {', '.join(bad)}: a judge: metric reads hits_judged.json, and the only one "
                           f"accepted is {', '.join(PREFIX + f for f in FIELDS)}")
    return names


def read_judged(cell_dir: Path, rec: dict[str, Any], metrics: tuple[str, ...]) -> None:
    """Put the cell's ``judge:`` metrics in ``rec["metrics"]`` (left out when it does not report them) and its
    reason in ``rec["judged"]``; nothing when none is asked for."""
    names = judge_names(metrics)
    if not names:
        return
    value, rec["judged"] = _judged(cell_dir, rec)
    for name in names:
        rec["metrics"].pop(name, None)  # a grade.json metric of this name is not the judge's
        if value is not None:
            rec["metrics"][name] = value


def _judged(cell_dir: Path, rec: dict[str, Any]) -> tuple[float | None, str]:
    path = cell_dir / HITS_JUDGED
    if not os.path.lexists(path):
        return None, "no_file"
    doc = _json(path)
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSIONS["repo_hits_judged"]:
        return None, "unreadable"
    if any(doc.get(k) != rec.get(k) for k in ("cell", "task", "arm", "model")):
        return None, "other_cell"
    recall = as_measure(doc.get("recall"))
    metrics, matches = _grade(cell_dir / "grade.json")
    saved = _pairs(doc.get("pairs"))
    if (recall is None or recall != as_measure(metrics.get("recall")) or doc.get("n_threads") != metrics.get("n_threads")
            or saved is None or saved != _pairs(matches)):
        return None, "stale"
    if doc.get("recall_judged") is None:
        return None, "unjudged"
    value = as_measure(doc["recall_judged"])
    return value, "read" if value is not None else "unreadable"


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):  # a directory, not UTF-8, not JSON, nested too deep
        return None


def _grade(path: Path) -> tuple[dict[str, Any], Any]:
    """The grade's ``metrics`` and ``details.matches`` (``{}`` and ``None`` when it has none)."""
    grade = _json(path)
    grade = grade if isinstance(grade, dict) else {}
    metrics, details = grade.get("metrics"), grade.get("details")
    return (metrics if isinstance(metrics, dict) else {}), (details.get("matches") if isinstance(details, dict) else None)


def _pairs(items: Any) -> list[tuple[Any, Any]] | None:
    """The ``(thread, finding)`` of each entry, in order; no entries (a parse-error cell) is an empty list."""
    if items is None:
        return []
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        return None
    return [(i.get("thread"), i.get("finding")) for i in items]


def check_reported(metrics: tuple[str, ...], cells: list[dict[str, Any]], scope: str = "") -> None:
    """A ``judge:`` metric no selected cell reports is refused with the command that writes the files."""
    for name in judge_names(metrics):
        if not any(name in c["metrics"] for c in cells):
            why = _reasons(_counts(cells))
            raise CompareError(f"no finished cell reports --metric {name}{scope}{f' ({why})' if why else ''}: "
                               f"{HINT} first")


def judged_cells(metrics: tuple[str, ...], arms: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """Per compared arm, its cells and the count of each of :data:`REASONS`; ``None`` without a ``judge:`` metric."""
    if not judge_names(metrics):
        return None
    return {"metrics": judge_names(metrics), "arms": {text: _counts(cells) for text, cells in arms.items()}}


def _counts(cells: list[dict[str, Any]]) -> dict[str, int]:
    return {"cells": len(cells), **{r: sum(c.get("judged") == r for c in cells) for r in REASONS}}


def _reasons(counts: dict[str, int]) -> str:
    return ", ".join(f"{counts[r]} {_SAID[r]}" for r in REASONS[1:] if counts[r])


def judged_lines(judged: dict[str, Any] | None) -> list[str]:
    """One line per arm with a cell not read, and the note that compare does not calibrate the judge."""
    if not judged:
        return []
    name = ", ".join(judged["metrics"])
    lines = [f"{name}, {text}: {c['read']} of {c['cells']} cells read ({_reasons(c)}); run repo hits judge again"
             for text, c in judged["arms"].items() if c["read"] < c["cells"]]
    return lines + [f"note: {name} is a judge's labels of the credited pairs, at most recall; it is uncalibrated "
                    "unless repo hits judge --labels reported agreement for these arms (contract §9)"]
