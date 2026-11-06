# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``dokimi:hidden_tests``: the repository's own tests, held out of the workspace, pass (contract §4.5).

Before this grader runs, the framework copies the task's ``hidden/`` files (``task.json``
``hidden_files``) into the workspace and runs the manifest's ``entry_points.test`` on
``hidden_tests`` in the grading container; that command writes a JUnit XML report to ``{out}``
(:data:`REPORT` in the run dir), and the framework records its exit in :data:`RUN_RECORD`.
This module reads both: ``pass`` is true when the command exited 0, did not time out, and the
report holds at least one passed test case and no failed or errored one. ``pass_share`` is the
passed share of the cases that ran (skipped ones left out), for ``repo compare --metric``.

The oracle copies ``reference/`` over the workspace, as ``dokimi:typecheck``'s does, and deletes
``reference_deleted``. Standard library only, Python 3.8: it runs on the image's ``python3``.
"""

from __future__ import annotations

import importlib
import json
import os
import posixpath
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

if __package__:
    from . import typecheck
else:  # a script under -I, loaded by main.py, which put this directory on sys.path
    typecheck = importlib.import_module("typecheck")

# Bump when a result for the same inputs changes (a metric or ``pass``).
VERSION = "1"
HIDDEN_DIR = "hidden"  # task dir: the hidden files, at their repository paths
REPORT = "hidden-tests.xml"  # run dir: the JUnit XML report the test command writes to {out}
RUN_RECORD = "hidden-tests.json"  # run dir: the test command's exit, written by the framework
MAX_REPORT_BYTES = 64 * 1024 * 1024
MAX_LISTED = 50  # failed test ids kept in details


def _paths_problem(raw: dict[str, Any], key: str, required: bool) -> str | None:
    value = raw.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, list) or (required and not value) or not all(typecheck._safe_path(p) for p in value):
        return f"{key} must be a {'non-empty ' if required else ''}list of workspace-relative file paths"
    if len({posixpath.normpath(p) for p in value}) != len(value):
        return f"{key} must be distinct after normalising"
    return None


def task_problems(raw: dict[str, Any]) -> list[str]:
    """Problems with a ``dokimi:hidden_tests`` task's own fields (checked by ``repo validate`` and ``eval check``)."""
    problems = [p for p in (_paths_problem(raw, "hidden_tests", True), _paths_problem(raw, "hidden_files", False),
                            _paths_problem(raw, "reference_deleted", False)) if p]
    if not problems:
        files = {posixpath.normpath(p) for p in raw.get("hidden_files") or raw["hidden_tests"]}
        outside = [p for p in raw["hidden_tests"] if posixpath.normpath(p) not in files]
        if outside:
            problems.append(f"hidden_tests not in hidden_files: {', '.join(outside)}")
    return problems


def hidden_files(raw: dict[str, Any]) -> list[str]:
    """The files the framework copies from ``hidden/`` into the workspace: ``hidden_files``, else ``hidden_tests``."""
    return [posixpath.normpath(p) for p in raw.get("hidden_files") or raw.get("hidden_tests") or []]


def _record(run_dir: Path) -> dict[str, Any]:
    path = run_dir / RUN_RECORD
    if not path.is_file():
        raise ValueError(f"no {RUN_RECORD}: the test command did not run")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{RUN_RECORD} is not an object")
    if doc.get("problem"):  # the framework could not stage the hidden files
        raise ValueError(str(doc["problem"]))
    return doc


def _cases(run_dir: Path) -> tuple[list[tuple[str, str]], str | None]:
    """``[(test id, outcome)]`` of the JUnit report, outcome ``passed``, ``failed``, ``error`` or ``skipped``;
    and why there are none to read (no report, too large, not XML), or ``None``."""
    path = run_dir / REPORT
    if path.is_symlink() or not path.is_file():
        return [], f"the test command wrote no JUnit report to {REPORT}"
    if path.stat().st_size > MAX_REPORT_BYTES:
        return [], f"{REPORT} is over {MAX_REPORT_BYTES} bytes"
    data = path.read_bytes()
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:  # no entity expansion from a report the subject wrote
        return [], f"{REPORT} declares a DOCTYPE or an entity"
    try:  # stdlib only (no defusedxml in the image); with no DOCTYPE there is no entity to expand
        root = ElementTree.fromstring(data)  # nosec B314
    except ElementTree.ParseError as exc:
        return [], f"{REPORT} is not XML: {exc}"
    cases = []
    for case in root.iter("testcase"):
        name = "::".join(p for p in (case.get("classname") or "", case.get("name") or "") if p)
        kinds = {child.tag for child in case}
        outcome = ("error" if "error" in kinds else "failed" if "failure" in kinds
                   else "skipped" if "skipped" in kinds else "passed")
        cases.append((name, outcome))
    return cases, None


def grade(task: dict[str, Any], task_dir: Path, answer: str, run_dir: Path | None = None) -> dict[str, Any]:
    """The hidden-test run of ``run_dir`` as the grader's result dict."""
    del task_dir, answer  # the workspace and the report are the subject
    problems = task_problems(task)
    if problems:
        raise ValueError("; ".join(problems))
    rd = Path(run_dir or "/eval/run")
    record = _record(rd)
    cases, why = _cases(rd)
    count = {k: sum(o == k for _, o in cases) for k in ("passed", "failed", "error", "skipped")}
    ran = len(cases) - count["skipped"]
    rc, timed_out = record.get("rc"), record.get("timed_out") is True
    ok = rc == 0 and not timed_out and count["passed"] > 0 and count["failed"] == count["error"] == 0
    details: dict[str, Any] = {"not_passed": [n for n, o in cases if o in ("failed", "error")][:MAX_LISTED]}
    if why:
        details["report"] = why
    return {"pass": ok, "n_tests": len(cases), "n_passed": count["passed"], "n_failed": count["failed"],
            "n_errors": count["error"], "n_skipped": count["skipped"],
            "pass_share": count["passed"] / ran if ran else None,
            "tests_rc": rc if isinstance(rc, int) and not isinstance(rc, bool) else None,
            "tests_timed_out": timed_out, **details}


def oracle(task: dict[str, Any], task_dir: Path, run_dir: Path | None = None) -> str:
    """Copy ``reference/`` over the workspace (``dokimi:typecheck``'s oracle), then delete ``reference_deleted``;
    a path reached through a symbolic link in the workspace is refused. The answer is empty."""
    problems = task_problems(task)
    if problems:
        raise ValueError("; ".join(problems))
    typecheck.oracle(task, task_dir, run_dir)
    workspace = Path(run_dir or "/eval/run") / "workspace"
    for rel in task.get("reference_deleted") or []:
        dest = workspace / posixpath.normpath(rel)
        if typecheck._linked_on_the_way(workspace, dest.parent):
            raise ValueError(f"{rel} would be deleted through a symbolic link in the workspace")
        if dest.is_symlink() or dest.is_file():
            os.unlink(str(dest))
    return ""


GRADERS = {"hidden_tests": grade}
ORACLES = {"hidden_tests": oracle}
