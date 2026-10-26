# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Planned cells versus completed cells: the ``plan.v1`` / ``result.v1`` pair.

A plan lists every cell (arm x model x task x rep) a run promises to measure; a result
gives each cell one status. :func:`completeness` is the one place that decides whether
a run is complete, computed as planned minus completed. A run that finishes 2 of its 6
planned cells is ``incomplete`` however well those two did, and a result that points at
another plan (digest mismatch) or names cells the plan does not have is ``invalid``.

Everything here is pure: no file, model or process is touched. Reading and writing the
artifacts goes through :mod:`dokimi.artifacts` (kinds ``plan`` and ``result``).
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

#: Statuses that are a measurement of the cell. ``invalid``, ``infra_error`` and
#: ``not_run`` are recorded, but the cell still counts as missing.
MEASURED = frozenset({"pass", "fail", "inconclusive"})
STATUSES = ("pass", "fail", "inconclusive", "invalid", "infra_error", "not_run")


def cell_id(arm: str, model: str | None, task: str, rep: int) -> str:
    """Stable id of one cell: the same four coordinates give the same id in every plan."""
    key = json.dumps([arm, model, task, int(rep)], separators=(",", ":"), ensure_ascii=True)
    return "c-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def plan_cells(arms: Iterable[str], models: Iterable[str | None], tasks: Iterable[str],
               reps: int = 1) -> list[dict[str, Any]]:
    """The full arm x model x task x rep grid, in that nesting order, each with its id."""
    if reps < 1:
        raise ValueError(f"reps must be at least 1, got {reps}")
    return [
        {"cell_id": cell_id(arm, model, task, rep), "arm": arm, "model": model, "task": task, "rep": rep}
        for arm, model, task, rep in itertools.product(list(arms), list(models), list(tasks),
                                                       range(1, reps + 1))
    ]


def plan_digest(plan: Mapping[str, Any]) -> str:
    """``sha256:<hex>`` of the plan's canonical JSON, ``generated_at`` left out."""
    body = {k: v for k, v in plan.items() if k != "generated_at"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Completeness:
    """What :func:`completeness` found. ``state`` is ``complete``, ``incomplete`` or ``invalid``."""

    state: str
    planned: int
    measured: int
    missing: tuple[str, ...]
    statuses: dict[str, int]
    problems: tuple[str, ...] = field(default=())

    @property
    def complete(self) -> bool:
        return self.state == "complete"

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "planned": self.planned, "measured": self.measured,
                "missing": list(self.missing), "statuses": dict(self.statuses),
                "problems": list(self.problems)}


def _statuses(result_cells: Any, problems: list[str]) -> dict[str, str]:
    """``cell_id -> status`` of the result; an unknown status is appended to ``problems``."""
    status: dict[str, str] = {}
    for cell in result_cells if isinstance(result_cells, list) else []:
        cid = cell.get("cell_id") if isinstance(cell, Mapping) else None
        st = cell.get("status") if isinstance(cell, Mapping) else None
        if not isinstance(cid, str):
            continue  # reported by completeness
        if isinstance(st, str) and st in STATUSES:
            status[cid] = st
        else:
            problems.append(f"result cell {cid} has an unknown status: {st!r}")
    return status


def _ids(side: str, cells: Any, problems: list[str]) -> list[str]:
    """The string ``cell_id`` of each cell; a cell without one is a problem, not a crash."""
    if not isinstance(cells, list):
        problems.append(f"{side} cells is not a list: {cells!r}")
        return []
    ids: list[str] = []
    for cell in cells:
        cid = cell.get("cell_id") if isinstance(cell, Mapping) else None
        if isinstance(cid, str):
            ids.append(cid)
        else:
            problems.append(f"{side} has a cell without a string cell_id: {cell!r}")
    return ids


def completeness(plan: Mapping[str, Any], result: Mapping[str, Any]) -> Completeness:
    """Planned minus completed cells of ``result`` against ``plan``.

    - ``invalid``: the result's ``plan_digest`` is not this plan's, the plan repeats a cell
      id, ``cells`` is not a list, a cell has no string ``cell_id``, or a result cell has a
      status outside :data:`STATUSES`; or the result names a cell the plan lacks or gives one
      cell two statuses. An ``invalid`` result reports ``measured=0``.
    - ``incomplete``: some planned cell has no measured status (absent, ``not_run``,
      ``infra_error`` or ``invalid``); those ids are in ``missing``, in plan order.
    - ``complete``: every planned cell is ``pass``, ``fail`` or ``inconclusive``. Complete
      says nothing about whether the run passed; that is a gate's call.
    """
    problems: list[str] = []
    planned = _ids("plan", plan.get("cells", []), problems)
    expected, actual = plan_digest(plan), result.get("plan_digest")
    if actual != expected:
        problems.append(f"result is for plan {actual}, not {expected}")
    dup_planned = sorted(k for k, n in Counter(planned).items() if n > 1)
    if dup_planned:
        problems.append(f"plan repeats cell ids: {dup_planned}")
    result_cells = result.get("cells", [])
    seen = Counter(_ids("result", result_cells, problems))
    dup_result = sorted(k for k, n in seen.items() if n > 1)
    if dup_result:
        problems.append(f"result gives more than one status to cells: {dup_result}")
    unplanned = sorted(set(seen) - set(planned))
    if unplanned:
        problems.append(f"result has cells the plan does not: {unplanned}")
    status = _statuses(result_cells, problems)
    counts = Counter(status.get(cid, "not_run") for cid in dict.fromkeys(planned))
    missing = tuple(cid for cid in dict.fromkeys(planned) if status.get(cid, "not_run") not in MEASURED)
    if problems:
        state = "invalid"
    elif missing or not planned:
        state = "incomplete"
    else:
        state = "complete"
    measured = 0 if problems else len(set(planned)) - len(missing)
    return Completeness(state=state, planned=len(set(planned)), measured=measured,
                        missing=missing, statuses={s: counts[s] for s in STATUSES if counts[s]},
                        problems=tuple(problems))
