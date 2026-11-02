# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The built-in grader ``dokimi:typecheck`` (contract §4.5): listed files type-check clean under the pinned mypy."""

from __future__ import annotations

import json
import os
import sys
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


def test_grading_leaves_the_recursion_limit_as_it_was(tmp_path):
    # mypy's main raises the process-wide recursion limit; in-process, that leaked into later code.
    before = sys.getrecursionlimit()
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": CLEAN}, _task())
    typecheck.grade(_task(), tmp_path, "", run)
    assert sys.getrecursionlimit() == before


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
    {"paths": ["odd\nname/api.py"]},  # a line separator splits the name in mypy's output
    {"paths": ["odd\u2028name/api.py"]},
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


def test_the_subjects_mypy_ini_cannot_suppress_a_listed_file(tmp_path):
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": WRONG,
                          "mypy.ini": "[mypy-pkg.api]\nignore_errors = True\n"}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is False
    assert result["n_errors"] == 1


def test_a_colored_mypy_image_still_fails_a_listed_file_with_a_type_error(tmp_path, monkeypatch):
    # the image's env is kept for the grade: MYPY_FORCE_COLOR puts ANSI escapes around the file name and the marker
    monkeypatch.setenv("MYPY_FORCE_COLOR", "1")
    run = _run(tmp_path, {"pkg/api.py": WRONG}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is False
    assert result["n_errors"] == 1


def test_a_listed_path_with_a_colon_is_matched():
    out = typecheck._errors("a:b.py:3: error: boom  [misc]", {"a:b.py"}, "")
    assert [(e["file"], e["line"]) for e in out] == [("a:b.py", 3)]


def test_a_line_less_error_on_a_listed_file_is_counted():
    out = typecheck._errors("pkg/a.py: error: Duplicate module named 'a'", {"pkg/a.py"}, "")
    assert [(e["file"], e["line"]) for e in out] == [("pkg/a.py", 0)]


NAMESPACE_API = "from pkg.other import broken\n\ndef use() -> int:\n    return broken()\n"
NAMESPACE_OTHER = "def broken() -> int:\n    return 1\n"


def test_a_relative_run_directory_is_graded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": CLEAN}, _task())
    result = typecheck.grade(_task(), tmp_path, "", Path("run"))
    assert result["pass"] is True
    assert result["missing"] == []


def test_a_namespace_package_resolves_from_the_workspace_root(tmp_path):
    # both files listed: a listed other.py must be named pkg.other, the name api.py imports it by
    task = _task(paths=["pkg/api.py", "pkg/other.py"])
    run = _run(tmp_path, {"pkg/api.py": NAMESPACE_API, "pkg/other.py": NAMESPACE_OTHER}, task)
    result = typecheck.grade(task, tmp_path, "", run)
    assert result["pass"] is True, result["errors"]


def test_a_src_layout_package_resolves_when_both_files_are_listed(tmp_path):
    # a regular package under src/: pkg.x is imported from src/, so src/ is a root with the workspace
    files = {"src/pkg/__init__.py": "", "src/pkg/x.py": "val: int = 1\n",
             "src/pkg/y.py": "from pkg.x import val\n\nprint(val + 1)\n"}
    task = _task(paths=["src/pkg/x.py", "src/pkg/y.py"])
    run = _run(tmp_path, files, task)
    result = typecheck.grade(task, tmp_path, "", run)
    assert result["pass"] is True, result["errors"]


def test_a_blocking_syntax_error_in_a_listed_file_fails_the_grade(tmp_path):
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": "def broken(:\n    pass\n"}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is False
    assert result["n_errors"] >= 1
    assert result["errors"][0].startswith("pkg/api.py:1:")


def test_mypy_failing_without_a_diagnostic_is_a_grader_error(tmp_path, monkeypatch):
    run = _run(tmp_path, {"pkg/api.py": CLEAN}, _task())
    import mypy.api

    monkeypatch.setattr(mypy.api, "run", lambda args: ("", "cannot start", 2))
    with pytest.raises(RuntimeError, match="mypy exited 2"):
        typecheck.grade(_task(), tmp_path, "", run)


def test_the_reference_directory_repairs_the_workspace_before_grading(tmp_path):
    task_dir = tmp_path / "task"
    (task_dir / "reference" / "pkg").mkdir(parents=True)
    (task_dir / "reference" / "pkg" / "api.py").write_text(CLEAN, encoding="utf-8")
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": WRONG}, _task())
    assert typecheck.grade(_task(), tmp_path, "", run)["pass"] is False
    assert typecheck.oracle(_task(), task_dir, run) == ""
    assert typecheck.grade(_task(), tmp_path, "", run)["pass"] is True


def test_the_entry_point_oracle_repairs_the_workspace_too(tmp_path):
    task_dir = tmp_path / "task"
    (task_dir / "reference" / "pkg").mkdir(parents=True)
    (task_dir / "reference" / "pkg" / "api.py").write_text(CLEAN, encoding="utf-8")
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": WRONG}, _task())
    main.oracle("typecheck", task_dir, run)
    assert main.grade("typecheck", task_dir, run)["pass"] is True


def test_a_blocking_error_in_an_imported_module_fails_the_listed_file(tmp_path):
    # mypy stops at the syntax error and never reaches api.py, so the listed file is not checked
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/other.py": "def broken(:\n    pass\n",
                          "pkg/api.py": "from pkg.other import broken\n\ndef use() -> int:\n    return 'x'\n"},
               _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is False
    assert result["n_errors"] >= 1


def test_an_external_mypypath_cannot_supply_a_module_the_image_lacks(tmp_path, monkeypatch):
    stubs = tmp_path / "stubs"  # outside the workspace, as an image's or the environment's MYPYPATH would be
    stubs.mkdir()
    (stubs / "unavailable.pyi").write_text("def value() -> int: ...\n", encoding="utf-8")
    monkeypatch.setenv("MYPYPATH", str(stubs))
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": "from unavailable import value\n"}, _task())
    result = typecheck.grade(_task(), tmp_path, "", run)
    assert result["pass"] is False
    assert any("unavailable" in error for error in result["errors"])
    assert os.environ["MYPYPATH"] == str(stubs)  # restored for the caller


@pytest.mark.parametrize("linked", ["file", "directory", "workspace"])
def test_the_oracle_refuses_to_write_through_a_symbolic_link(tmp_path, linked):
    outside = tmp_path / "outside"
    (outside / "pkg").mkdir(parents=True)
    sentinel = outside / "pkg" / "api.py"
    sentinel.write_text("ORIGINAL", encoding="utf-8")
    task_dir = tmp_path / "task"
    (task_dir / "reference" / "pkg").mkdir(parents=True)
    (task_dir / "reference" / "pkg" / "api.py").write_text("REFERENCE", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    workspace = run / "workspace"
    if linked == "file":
        (workspace / "pkg").mkdir(parents=True)
        (workspace / "pkg" / "api.py").symlink_to(sentinel)
    elif linked == "directory":
        workspace.mkdir()
        (workspace / "pkg").symlink_to(outside / "pkg", target_is_directory=True)
    else:
        workspace.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        typecheck.oracle(_task(), task_dir, run)
    assert sentinel.read_text(encoding="utf-8") == "ORIGINAL"


def test_the_oracle_refuses_a_linked_reference_directory_with_a_linked_destination(tmp_path):
    # copytree follows a linked reference directory and writes its entries through a linked destination
    # file; the walk of the reference does not see inside the link, so the link itself is refused.
    repairs = tmp_path / "repairs"
    repairs.mkdir()
    (repairs / "api.py").write_text("REFERENCE", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "api.py"
    sentinel.write_text("ORIGINAL", encoding="utf-8")
    task_dir = tmp_path / "task"
    (task_dir / "reference").mkdir(parents=True)
    (task_dir / "reference" / "pkg").symlink_to(repairs, target_is_directory=True)
    run = tmp_path / "run"
    (run / "workspace" / "pkg").mkdir(parents=True)
    (run / "workspace" / "pkg" / "api.py").symlink_to(sentinel)
    with pytest.raises(ValueError, match="symbolic link"):
        typecheck.oracle(_task(), task_dir, run)
    assert sentinel.read_text(encoding="utf-8") == "ORIGINAL"


def test_the_oracle_copies_the_reference_when_no_link_is_on_the_way(tmp_path):
    task_dir = tmp_path / "task"
    (task_dir / "reference" / "pkg").mkdir(parents=True)
    (task_dir / "reference" / "pkg" / "api.py").write_text(CLEAN, encoding="utf-8")
    run = _run(tmp_path, {"pkg/__init__.py": "", "pkg/api.py": WRONG}, _task())
    typecheck.oracle(_task(), task_dir, run)
    assert (run / "workspace" / "pkg" / "api.py").read_text(encoding="utf-8") == CLEAN


def test_the_oracle_refuses_an_empty_reference_directory_created_through_a_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    task_dir = tmp_path / "task"
    (task_dir / "reference" / "pkg" / "sub").mkdir(parents=True)  # no file under it
    run = tmp_path / "run"
    (run / "workspace").mkdir(parents=True)
    (run / "workspace" / "pkg").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        typecheck.oracle(_task(), task_dir, run)
    assert not (outside / "sub").exists()


def test_the_oracle_refuses_a_linked_workspace_even_with_an_empty_reference(tmp_path):
    # no entry in reference/, so the walk checks nothing; copytree still copies the mode onto the linked root
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    task_dir = tmp_path / "task"
    (task_dir / "reference").mkdir(parents=True)
    run = tmp_path / "run"
    run.mkdir()
    (run / "workspace").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        typecheck.oracle(_task(), task_dir, run)
    assert outside.stat().st_mode & 0o777 == 0o700


def test_linked_on_the_way_sees_a_link_in_the_middle_of_a_path(tmp_path):
    # the oracle's walk also reaches this through the reference's directories; the helper is pinned on its own
    outside = tmp_path / "outside"
    (outside / "c").mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a").symlink_to(outside, target_is_directory=True)
    assert typecheck._linked_on_the_way(workspace, workspace / "a" / "b" / "c.py") is True
    assert typecheck._linked_on_the_way(workspace, workspace / "d" / "c.py") is False


def test_check_task_runs_no_oracle_step_for_a_typecheck_task_without_reference(tmp_path, monkeypatch):
    # the gate in check_task itself: a mutated `_oracle_applies(...) and` would run the oracle here
    from types import SimpleNamespace

    from dokimi.repos import selfcheck
    from dokimi.repos.workspace import Task

    bare = tmp_path / "bare"
    bare.mkdir()
    raw = {"grader": "dokimi:typecheck", "tool": "mypy", "version": "1", "paths": ["pkg/api.py"]}
    ran = []
    monkeypatch.setattr(selfcheck, "_host_reason", lambda ctx, task, work_dir, host: (None, None, None))
    monkeypatch.setattr(selfcheck, "_check_step",
                        lambda ctx, task, rd, kind, run_it, net, res: ran.append((kind, run_it)))
    monkeypatch.setattr(selfcheck, "isolation_probe", lambda *args, **kwargs: [])
    ctx = SimpleNamespace(manifest=SimpleNamespace(image=None))
    res = selfcheck.check_task(ctx, Task("t", raw, bare), tmp_path / "work")
    assert ran == [("null", True), ("oracle", False)]
    assert res["verdict"] == "ok"


def test_check_task_runs_the_oracle_step_for_a_typecheck_task_with_reference(tmp_path, monkeypatch):
    # the mirror of the test above: a `False and (...)` gate would skip the oracle of a task that has a repair
    from types import SimpleNamespace

    from dokimi.repos import selfcheck
    from dokimi.repos.workspace import Task

    with_reference = tmp_path / "with"
    (with_reference / "reference").mkdir(parents=True)
    raw = {"grader": "dokimi:typecheck", "tool": "mypy", "version": "1", "paths": ["pkg/api.py"]}
    ran = []
    monkeypatch.setattr(selfcheck, "_host_reason", lambda ctx, task, work_dir, host: (None, None, None))
    monkeypatch.setattr(selfcheck, "_check_step",
                        lambda ctx, task, rd, kind, run_it, net, res: ran.append((kind, run_it)))
    monkeypatch.setattr(selfcheck, "isolation_probe", lambda *args, **kwargs: [])
    ctx = SimpleNamespace(manifest=SimpleNamespace(image=None))
    selfcheck.check_task(ctx, Task("t", raw, with_reference), tmp_path / "work")
    assert ran == [("null", True), ("oracle", True)]


def test_a_typecheck_task_without_reference_runs_no_oracle_step(tmp_path):
    # reference/ is optional: without it the grade reads the workspace as it is, and the self-check has no
    # repair to apply, so it runs the null step alone (the untouched listed files must fail the gate).
    from dokimi.repos import selfcheck
    from dokimi.repos.workspace import Task

    bare = tmp_path / "bare"
    bare.mkdir()
    with_reference = tmp_path / "with"
    (with_reference / "reference").mkdir(parents=True)
    raw = {"grader": "dokimi:typecheck", "tool": "mypy", "version": "1", "paths": ["pkg/api.py"]}
    assert selfcheck._oracle_applies(Task("t", raw, bare)) is False
    assert selfcheck._oracle_applies(Task("t", raw, with_reference)) is True
    assert selfcheck._oracle_applies(Task("t", {"grader": "dokimi:review_seeds"}, bare)) is True
    assert selfcheck._oracle_applies(Task("t", {}, bare)) is True
