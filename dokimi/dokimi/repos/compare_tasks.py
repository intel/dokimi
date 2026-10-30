# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo compare --task``: the cells and missing cells a task selection keeps (:mod:`.compare`)."""

from __future__ import annotations

from typing import Any


class TaskSelectionError(ValueError):
    """A ``--task`` id that no finished cell of the batches has."""


def select_tasks(cells: list[dict[str, Any]], missing: list[str],
                 tasks: list[str] | None) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    """The cells and missing cells of ``tasks`` (``--task``), and the selection as the summary records it.

    No ``tasks`` keeps every cell and records ``None``. An id no finished cell of the batches has is refused."""
    if not tasks:
        return cells, missing, None
    known = {c["task"] for c in cells}
    unknown = [t for t in dict.fromkeys(tasks) if t not in known]
    if unknown:
        raise TaskSelectionError(f"no finished cell of the compared batches has --task {', '.join(unknown)}")
    kept = [m for m in missing if _cell_task(m.partition("/")[2]) in tasks]  # BATCH/<cell>
    return [c for c in cells if c["task"] in tasks], kept, {"tasks": sorted(set(tasks)), "of": len(known)}


def _cell_task(cell: str) -> str | None:
    """The task of a cell name ``<task>__<arm>__<model>__r<rep>`` (:func:`~dokimi.repos.runner.cell_name`).

    A task id may itself contain ``__``, so the three parts after it are split off from the right; the arm and
    model are slugs, which the split assumes carry no ``__``. ``None`` for a name of another shape.
    """
    parts = cell.rsplit("__", 3)
    return parts[0] if len(parts) == 4 else None
