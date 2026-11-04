# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run a built-in grader in the grading container (contract §4.5).

``python3 -I main.py grade|oracle <name> <task_dir> [<run_dir>]``

- ``grade`` reads ``task.json`` and ``answer.txt`` from ``run_dir`` (default
  ``/eval/run``) and the task's data from ``task_dir`` (its directory under
  ``/eval/content``), and writes ``run_dir/grade.json`` as ``repo_grade.v1``:
  numbers, booleans and null under ``metrics``; strings, lists and objects
  under ``details``. A grader that raises writes ``pass: null`` with
  ``errors``, which the framework counts as a grader failure, and exits 0.
- ``oracle`` writes the reference answer to ``run_dir/answer.txt``.

The framework mounts this directory read-only and runs it with ``-I``, so
neither ``PYTHONPATH`` nor the workspace can put a module in front of the
standard library. Standard library only: it runs on the image's ``python3``.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from pathlib import Path

if __package__:
    from . import review, typecheck
else:  # a script under -I: its own directory is not on sys.path
    # By name, not `import review`: dependency hygiene (deptry) reads a bare import as a third-party package.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    review = importlib.import_module("review")
    typecheck = importlib.import_module("typecheck")

GRADERS = {**review.GRADERS, **typecheck.GRADERS}
ORACLES = {**review.ORACLES, **typecheck.ORACLES}
VERSIONS = {**{name: review.VERSION for name in review.GRADERS}, **{name: typecheck.VERSION for name in typecheck.GRADERS}}
RUN_DIR = "/eval/run"


def split_result(g):
    """``repo_grade.v1``: numbers, booleans and null under metrics; strings, lists and objects under details."""
    mets, det = {}, {}
    for k, v in g.items():
        if k == "pass":
            continue
        if v is None or isinstance(v, (bool, int, float)):
            mets[k] = v
        else:
            det[k] = v
    return mets, det


def _read_task(run_dir):
    with open(str(run_dir / "task.json"), encoding="utf-8") as fh:
        return json.load(fh)


def gate_names(task):
    """Metric names the task's ``gate`` reads (``pass`` is not a metric)."""
    out = []
    for entry in task.get("gate", []):
        name = entry.get("metric") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name != "pass":
            out.append(name)
    return out


def grade(name, task_dir, run_dir):
    """Grade the run in ``run_dir`` and write its ``grade.json``; returns the document."""
    t = _read_task(run_dir)
    out = {"schema": "repo_grade.v1", "task": t.get("id"), "grader": t.get("grader"), "pass": None,
           "metrics": {}, "details": {}, "errors": []}
    try:
        answer = run_dir / "answer.txt"
        text = answer.read_text(encoding="utf-8") if answer.exists() else ""
        g = GRADERS[name](t, task_dir, text, run_dir)
        out["metrics"], out["details"] = split_result(g)
        for k in gate_names(t):  # a gate metric the grader did not reach is unmeasured, not failed
            out["metrics"].setdefault(k, None)
        out["pass"] = g.get("pass")
    except Exception as e:  # noqa: BLE001  # reported as not graded, never as a failure
        out.update(metrics={}, details={}, errors=[f"{type(e).__name__}: {e}", traceback.format_exc(limit=5)])
        out["pass"] = None
    out["details"]["grader_version"] = VERSIONS[name]
    with open(str(run_dir / "grade.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    return out


def oracle(name, task_dir, run_dir):
    """Write the reference answer to ``run_dir/answer.txt``; returns it."""
    text = ORACLES[name](_read_task(run_dir), task_dir, run_dir)
    if text:
        (run_dir / "answer.txt").write_text(text, encoding="utf-8")
    return text


def main(argv):
    if len(argv) not in (3, 4) or argv[0] not in ("grade", "oracle") or argv[1] not in GRADERS:
        sys.stderr.write("usage: main.py grade|oracle {%s} <task_dir> [<run_dir>]\n" % "|".join(sorted(GRADERS)))
        return 2
    run_dir = Path(argv[3] if len(argv) == 4 else RUN_DIR)
    (grade if argv[0] == "grade" else oracle)(argv[1], Path(argv[2]), run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
