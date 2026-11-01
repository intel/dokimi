# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The built-in grader ``dokimi:typecheck`` (contract §4.5): listed files type-check clean under the pinned mypy."""

from __future__ import annotations

import json
from pathlib import Path

import mypy.version
import pytest

from dokimi.repos.graders import main, typecheck

PINNED = mypy.version.__version__


def _task(**over) -> dict:
    task = {"id": "typed", "grader": "dokimi:typecheck", "version": PINNED, "paths": ["pkg/api.py"]}
    task.update(over)
    return task


def _run(tmp_path: Path, files: dict[str, str], task: dict) -> Path:
    """A run directory whose workspace holds ``files``; returns the run directory."""
    run = tmp_path / "run"
    for name, text in files.items():
        path = run / "workspace" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (run / "task.json").write_text(json.dumps(task), encoding="utf-8")
    return run


CLEAN = "def add(a: int, b: int) -> int:\n    return a + b\n"
WRONG = "def add(a: int, b: int) -> int:\n    return f'{a}{b}'\n"


def test_listed_file_that_type_checks_passes(tmp_path):
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": CLEAN}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is True
    assert result["n_errors"] == 0
    assert result["tool_version"] == PINNED


def test_listed_file_with_a_type_error_fails_and_names_the_error(tmp_path):
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": WRONG}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is False
    assert result["n_errors"] == 1
    assert result["errors"][0].startswith("pkg/api.py:2:")
    assert "[return-value]" in result["errors"][0]


def test_an_error_in_an_unlisted_module_is_not_counted(tmp_path):
    other = "def broken() -> int:\n    return 'no'\n"
    api = "from pkg.other import broken\n\ndef use() -> int:\n    return broken()\n"
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/other.py": other, "pkg/api.py": api}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is True
    assert result["n_errors"] == 0


def test_a_listed_file_that_is_missing_fails_without_running_the_tool(tmp_path):
    run = _run(tmp_path, {"pkg/__init__.py": ""}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is False
    assert result["missing"] == ["pkg/api.py"]
    assert result["n_errors"] == 0


def test_a_pinned_version_other_than_the_image_is_a_grader_error(tmp_path):
    run = _run(tmp_path, {"pkg/api.py": CLEAN}, _task(version="0.0.1"))
    with pytest.raises(ValueError, match="pins 0.0.1"):
        typecheck.grade(_task(version="0.0.1"), tmp_path, "", run)


def test_the_entry_point_turns_a_grader_error_into_pass_none_with_errors(tmp_path):
    run = _run(tmp_path, {"pkg/api.py": CLEAN}, _task(version="0.0.1"))
    out = main.grade("typecheck", tmp_path, run)
    assert out["pass"] is None
    assert out["errors"] and "pins 0.0.1" in out["errors"][0]
    assert json.loads((run / "grade.json").read_text(encoding="utf-8"))["pass"] is None


def test_the_entry_point_writes_the_metrics_that_the_gate_reads(tmp_path):
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": WRONG}, _task())
    out = main.grade("typecheck", tmp_path, run)
    assert out["pass"] is False
    assert out["metrics"]["n_errors"] == 1
    assert out["metrics"]["n_missing"] == 0
    assert out["details"]["grader_version"] == typecheck.VERSION


@pytest.mark.parametrize("over", [
    {"tool": "basedpyright"},
    {"version": ""},
    {"version": None},
    {"paths": []},
    {"paths": ["/etc/passwd"]},
    {"paths": ["../outside.py"]},
    {"paths": "pkg/api.py"},
])
def test_task_problems_refuse_bad_fields(over):
    task = _task()
    task.update(over)
    assert typecheck.task_problems(task)


def test_task_problems_accept_a_well_formed_task():
    assert typecheck.task_problems(_task()) == []


def test_the_manifest_checks_the_task_fields_at_parse_time():
    from dokimi.repos.manifest import _builtin_problems

    raw = _task(version="")
    assert _builtin_problems(None, "tasks/typed", raw, "typecheck")
    assert _builtin_problems(None, "tasks/typed", _task(), "typecheck") == []


def test_the_grader_script_loads_its_siblings_by_name(monkeypatch):
    import runpy
    import sys

    from dokimi.repos.graders import main as grader_main

    monkeypatch.setattr(sys, "path", list(sys.path))
    for name in ("review", "typecheck"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    script = runpy.run_path(grader_main.__file__, run_name="grader_script")  # a script: no package
    assert script["GRADERS"]["typecheck"] is script["typecheck"].GRADERS["typecheck"]
    assert script["VERSIONS"]["typecheck"] == typecheck.VERSION
