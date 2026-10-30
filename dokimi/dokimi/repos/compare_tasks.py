# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo compare --task``: the cells and missing cells a task selection keeps (:mod:`.compare`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CompareError(ValueError):
    """The arms or batches named cannot be compared (here, a ``--task`` id; :mod:`.compare` raises the others)."""


def select_tasks(cells: list[dict[str, Any]], missing: list[str], tasks: list[str] | None,
                 batch_dirs: list[Path]) -> tuple[list[dict[str, Any]], list[str], dict[str, Any] | None]:
    """The cells and missing cells of ``tasks`` (``--task``), and the selection as the summary records it.

    No ``tasks`` keeps every cell, a missing cell of no known task too, and records ``None``. An id no finished
    cell of the batches has is refused."""
    if not tasks:
        return cells, missing, None
    known = {c["task"] for c in cells}
    unknown = [t for t in dict.fromkeys(tasks) if t not in known]
    if unknown:
        raise CompareError(f"no finished cell of the compared batches has --task {', '.join(unknown)}")
    dirs = {d.resolve().name: d for d in batch_dirs}
    ids = {name: {c["task"] for c in cells if c["batch"] == name} for name in dirs}
    kept = []
    for entry in missing:  # BATCH/<cell>
        batch, _, cell = entry.partition("/")
        # a cell whose task neither its meta.json nor a known id gives is not in any selection
        if _missing_task(dirs[batch], cell, ids[batch]) in tasks:
            kept.append(entry)
    return [c for c in cells if c["task"] in tasks], kept, {"tasks": sorted(set(tasks)), "of": len(known)}


def _missing_task(batch_dir: Path, cell: str, known: set[str]) -> str | None:
    """The task of a missing cell: the id its ``meta.json`` records (``repo run`` writes it when the cell starts),
    else, for a cell only ``plan.json`` names, the longest of the batch's finished task ids ``known`` that the
    name starts with followed by ``__``. The name alone does not say: :func:`~dokimi.repos.runner.cell_name`
    keeps a ``__`` of the task id, the arm and the model. ``None`` when neither gives a task."""
    try:
        meta = json.loads((batch_dir / cell / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        meta = None
    task = meta.get("task") if isinstance(meta, dict) else None
    if isinstance(task, str):
        return task
    return max((t for t in known if cell.startswith(f"{t}__")), key=len, default=None)
