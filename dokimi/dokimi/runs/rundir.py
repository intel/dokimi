# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run's own directory: ``run.json``, its plan, its result and its artifacts together (#94 R2).

:func:`open_run` makes the directory and writes ``run.json`` (``run.v1``) with status ``running``.
Used as a context manager, the :class:`RunDir` records how the run ended. On a stop (SIGTERM or
SIGHUP, ``KeyboardInterrupt``, the spend cap, a run-wide CLI failure) it writes ``result.v1.json`` with
``not_run`` on every planned cell that has no status, and ``run.json`` says ``incomplete``; any
other exception makes it ``failed``. Layout and rules: ``docs/run-directories.md``.
"""

from __future__ import annotations

import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from dokimi import process, process_hooks
from dokimi.artifacts import SCHEMA_VERSIONS, is_run_id, run_id, save_artifact
from dokimi.runner.plan import STATUSES, completeness, plan_digest
from dokimi.utils import InputError

#: The directory's own record, ``run.v1``.
RUN_FILE = "run.json"
Status = Literal["complete", "incomplete", "failed"]
StopReason = Literal["interrupted", "terminated", "spend_cap", "no_price", "run_stopped", "error"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def open_run(root: Path | str, name: str | None = None, out_dir: Path | str | None = None) -> RunDir:
    """Make a run's directory and write its ``run.json`` with status ``running``.

    The directory is ``out_dir`` when given (``--out-dir`` is the run directory, #591), else
    ``<root>/<name>``, else ``<root>/<run_id>`` (:func:`dokimi.artifacts.run_id`). A run never
    writes into another run's directory: the directory must not exist (``out_dir`` may be an
    empty directory), and ``run.json`` is created exclusively, so of two runs racing for one
    directory one gets it.

    Raises:
        InputError: ``name`` is not one path segment of a run id's characters; ``name`` and
            ``out_dir`` are both given (a name is unique only under the root); the directory
            exists (for ``out_dir``: and is not an empty directory) or another run claimed it;
            the directory cannot be made (a parent is a file, no permission).
    """
    if name is not None and not is_run_id(name):
        raise InputError(f"run name {name!r}: 1-128 letters, digits, '.', '_' or '-', starting with a "
                         "letter or a digit (docs/run-directories.md)")
    if name is not None and out_dir is not None:
        raise InputError("a run name names its directory under the runs root; with an output directory "
                         "the directory is that one: give one of them (docs/run-directories.md)")
    path = Path(out_dir) if out_dir is not None else Path(root) / (name or run_id())
    _claim(path, empty_ok=out_dir is not None)
    run = RunDir(path, {"name": name, "argv": sys.argv[1:], "started_at": _now(), "finished_at": None,
                        "status": "running", "stop": None})
    run._save(run._record)
    return run


def _claim(path: Path, *, empty_ok: bool) -> None:
    """Create ``path`` (or take an empty directory there when ``empty_ok``), then create its
    ``run.json`` exclusively: the file, not the directory, decides which run owns it."""
    taken = f"run directory {path} already exists: a run never writes into another run's directory"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # a file where a parent directory should be, no permission
        raise InputError(f"cannot make run directory {path}: {exc.strerror or exc}") from exc
    try:
        path.mkdir()
    except FileExistsError:
        if not (empty_ok and path.is_dir() and not any(path.iterdir())):
            raise InputError(taken) from None
    except OSError as exc:
        raise InputError(f"cannot make run directory {path}: {exc.strerror or exc}") from exc
    try:
        with open(path / RUN_FILE, "x", encoding="utf-8"):
            pass
    except FileExistsError:
        raise InputError(taken) from None


class RunDir:
    """One run's directory, made by :func:`open_run`; its methods may be called from any thread.

    In a ``with`` block, a block that ends normally finishes the run, ``complete`` or ``incomplete``
    by its cells, unless :meth:`finish` was called; a block that raises records the stop and re-raises.
    """

    def __init__(self, path: Path, record: dict[str, Any]) -> None:
        self.path = path
        self._record = record
        self._kinds: dict[str, str] = {}  # kind -> path relative to the directory
        self._plan: dict[str, Any] | None = None
        self._planned: list[str] = []
        self._cells: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()  # _end asks artifact_path for the result under it
        self._hooked = False
        self._signal: int | None = None  # the terminating signal that stopped the block

    @property
    def status(self) -> str:
        """``running`` until the run ends, then ``complete``, ``incomplete`` or ``failed``."""
        return str(self._record["status"])

    def artifact_path(self, kind: str) -> Path:
        """Where the run keeps its ``kind`` artifact: ``<schema_version>.json`` in the directory
        (``arms.v1.json``). ``run.json`` lists it, by its relative path, once the run ends and
        the file is there. ``KeyError`` for an unregistered kind, as :func:`save_artifact`."""
        name = f"{SCHEMA_VERSIONS[kind]}.json"
        with self._lock:
            self._kinds.setdefault(kind, name)
        return self.path / name

    def write_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        """Save ``plan`` (``plan.v1``) as the run's plan, whose cells a stop marks ``not_run``;
        return what was written."""
        written = save_artifact("plan", plan, self.artifact_path("plan"))
        with self._lock:
            self._plan, self._planned = written, [c["cell_id"] for c in written["cells"]]
        return written

    def record_cell(self, cell_id: str, status: str, *, model: str | None = None,
                    detail: str | None = None) -> None:
        """Give a planned cell its ``result.v1`` status; a later call for the cell replaces it.

        Raises:
            ValueError: ``status`` is not a ``result.v1`` status, or the plan has no such cell.
        """
        if status not in STATUSES:
            raise ValueError(f"{status!r} is not a result.v1 status: {', '.join(STATUSES)}")
        with self._lock:
            if cell_id not in self._planned:
                raise ValueError(f"cell {cell_id!r} is not in the plan of run {self.path}")
            self._cells[cell_id] = {"cell_id": cell_id, "status": status, "model": model, "detail": detail}

    def finish(self, status: Status) -> None:
        """End the run: ``result.v1.json`` when it has a plan, then ``run.json`` with ``status``.

        Raises:
            ValueError: the run has already ended, or ``status`` is ``complete`` while a planned
                cell has no measured status (result.v1: such a run is not complete).
        """
        self._end(status, None)

    def _end(self, status: Status, stop: dict[str, str | None] | None) -> None:
        with self._lock:
            if self._record["status"] != "running":
                raise ValueError(f"run {self.path} has already ended: {self._record['status']}")
            if self._plan is not None:
                result = self._result(self._plan, stop)
                if status == "complete" and not completeness(self._plan, result).complete:
                    raise ValueError(f"run {self.path} is not complete: planned cells have no measured "
                                     "status (result.v1)")
                save_artifact("result", result, self.artifact_path("result"))
            ended = {**self._record, "status": status, "finished_at": _now(), "stop": stop}
            self._save(ended)
            self._record = ended  # only once on disk: a failed write leaves the run running, to be recorded again

    def _measured(self) -> bool:
        """Whether every planned cell has a measured status (a run with no plan has none to measure)."""
        with self._lock:
            return self._plan is None or completeness(self._plan, self._result(self._plan, None)).complete

    def _result(self, plan: dict[str, Any], stop: dict[str, str | None] | None) -> dict[str, Any]:
        left = f"the run stopped before this cell ({stop['reason']})" if stop else None
        cells = [self._cells.get(cid) or {"cell_id": cid, "status": "not_run", "model": None, "detail": left}
                 for cid in dict.fromkeys(self._planned)]
        return {"generated_at": _now(), "plan_digest": plan_digest(plan), "cells": cells}

    def _save(self, record: dict[str, Any]) -> None:
        listed = [{"kind": kind, "path": rel} for kind, rel in self._kinds.items() if (self.path / rel).is_file()]
        save_artifact("run", {**record, "artifacts": listed}, self.path / RUN_FILE)

    def __enter__(self) -> RunDir:
        """On the main thread, until the block ends, the terminating-signal handler of
        :mod:`dokimi.process` (the CLI installs it) raises :class:`dokimi.process.ShuttingDown` in
        the block once it has killed the child process groups (:mod:`dokimi.process_hooks`)."""
        if threading.current_thread() is threading.main_thread():
            process_hooks.add(self._on_signal)
            self._hooked = True
        return self

    def _on_signal(self, signum: int) -> None:
        self._signal = signum
        raise process.ShuttingDown(f"{signal.Signals(signum).name}: the run stops")

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        """Record how the run ended, then deliver a signal that stopped it again, so the
        process ends as it would have without the run directory."""
        if self._hooked:
            process_hooks.remove(self._on_signal)
        try:
            if self.status == "running":
                if self._signal is not None:  # also when the block caught the ShuttingDown
                    self._end("incomplete", {"reason": "terminated",
                                             "detail": f"{signal.Signals(self._signal).name}: the run stops"})
                elif exc is None:
                    self.finish("complete" if self._measured() else "incomplete")
                else:
                    self._end(*_ending(exc))
        finally:
            if self._signal is not None:
                signal.raise_signal(self._signal)


def _ending(exc: BaseException) -> tuple[Status, dict[str, str | None]]:
    """How an exception that left a run's block ends it: ``incomplete`` with the stop's reason
    for the stops #94 R2 names, ``failed`` with ``error`` for anything else."""
    from dokimi.eval.arm_stop import RunStopped
    from dokimi.novelty.client import SpendCapReached

    detail = str(exc) or None
    if isinstance(exc, SpendCapReached):
        return "incomplete", {"reason": "no_price" if exc.reason == "no_price" else "spend_cap", "detail": detail}
    stops: tuple[tuple[type[BaseException], StopReason], ...] = (
        (KeyboardInterrupt, "interrupted"), (process.ShuttingDown, "terminated"), (RunStopped, "run_stopped"))
    for kind, reason in stops:
        if isinstance(exc, kind):
            return "incomplete", {"reason": reason, "detail": detail}
    return "failed", {"reason": "error", "detail": f"{type(exc).__name__}: {exc}"}
