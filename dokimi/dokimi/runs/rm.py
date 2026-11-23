# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What ``runs rm`` and ``runs gc`` remove from the runs root (#94 §2.3, R3).

A run is the artifacts that carry one ``run_id``, as ``runs list`` groups them: a file that is not
an artifact, or an artifact with no ``run_id``, belongs to no run, and only ``runs rm PATH``
removes it. Directories stay. Once a run has a directory of its own (#94 R2), that directory is
what a run removes. Nothing here prints or exits.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Mapping

from dokimi.artifacts import is_run_id
from dokimi.runs.scan import (
    Artifact,
    Unreadable,
    by_run,
    plain,
    read_error_reason,
    scan,
    timestamp,
)
from dokimi.runs.show import contained, find_run
from dokimi.utils import InputError

_AGE = re.compile(r"(\d{1,5})d")


@dataclass
class Removal:
    """What a removal takes away (or, before :func:`apply`, would): the artifacts of whole runs,
    newest first, or one file (``file``); paths are relative to ``root``."""

    root: Path
    runs: list[list[Artifact]] = field(default_factory=list)
    file: Path | None = None
    file_bytes: int = 0
    kept: dict[str, str] = field(default_factory=dict)  # run_id -> the baseline that holds it
    unreadable: list[Unreadable] = field(default_factory=list)  # left in place
    not_removed: list[Unreadable] = field(default_factory=list)  # filled by apply

    @property
    def paths(self) -> list[Path]:
        return [a.path for run in self.runs for a in run] + ([self.file] if self.file else [])

    @property
    def size_bytes(self) -> int:
        return sum(a.size_bytes for run in self.runs for a in run) + self.file_bytes


def parse_age(text: str) -> timedelta:
    """``30d`` as 30 days (#94 §2.3 spells the age so). Raises ``ValueError`` for anything else."""
    match = _AGE.fullmatch(text)
    if not match:
        raise ValueError(f"{text!r} is not an age in days, such as 30d")
    return timedelta(days=int(match.group(1)))


def _one_file(root: Path, address: str) -> Removal:
    """The file ``address`` names under ``root``, as ``runs show`` reads a path; a symlink is
    itself the file (its target is never removed). Raises ``InputError`` for anything else."""
    _, entry, found = contained(root, address, follow_last=False)
    if stat.S_ISDIR(found.st_mode):
        raise InputError(f"{plain(address)} is a directory; runs rm takes a run_id or one file")
    return Removal(root=root, file=Path(os.path.relpath(entry, os.path.realpath(root))),
                   file_bytes=found.st_size)


def plan_rm(root: Path, address: str) -> Removal:
    """The artifacts of the run ``address`` names, read as ``runs show`` reads it; or the one file.

    Raises ``InputError`` as :func:`dokimi.runs.show.find_run` does, and for a path that is not a
    file under the root.
    """
    run = find_run(root, address)
    if run is None:
        return _one_file(root, address)
    return Removal(root=root, runs=[run.artifacts], unreadable=run.unreadable)


def baseline_runs(entries: list[dict]) -> dict[str, str]:
    """The ``run_id`` each saved baseline copy carries -> the baseline's name; a copy saved before
    the envelope carries none. Raises ``InputError`` when a copy cannot be read, since the run it
    holds is then not known."""
    held: dict[str, str] = {}
    for entry in entries:
        try:
            data = json.loads(Path(entry["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            raise InputError(f"baseline {plain(entry['name'])}: {plain(entry['path'])}: "
                             f"{read_error_reason(exc)}, so the run it holds is not known") from exc
        run_id = data.get("run_id") if isinstance(data, dict) else None
        if isinstance(run_id, str) and is_run_id(run_id):
            held.setdefault(run_id, entry["name"])
    return held


def plan_gc(root: Path, cutoff: float, held: Mapping[str, str]) -> Removal:
    """The runs under ``root`` whose newest artifact is older than ``cutoff`` (POSIX seconds),
    bar those in ``held`` (run_id -> baseline name), which are named in ``kept``."""
    result = scan(root)
    removal = Removal(root=root, unreadable=result.unreadable)
    for run in by_run(result.artifacts):
        run_id = run[0].run_id
        if run_id is None or max(timestamp(a) for a in run) >= cutoff:
            continue
        if run_id in held:
            removal.kept[run_id] = held[run_id]
        else:
            removal.runs.append(run)
    return removal


def apply(removal: Removal) -> None:
    """Remove every path of ``removal``; one that cannot be removed goes to ``not_removed``.
    A path already gone counts as removed."""
    for path in removal.paths:
        try:
            (removal.root / path).unlink(missing_ok=True)
        except OSError as exc:
            removal.not_removed.append(Unreadable(path, f"cannot remove: {exc.strerror or exc}"))
