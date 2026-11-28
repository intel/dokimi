# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1210: a reader of a user's tree refuses a directory it cannot list, not a smaller result (as #1168 did for digests)."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path, PurePath, PureWindowsPath

import pytest

from dokimi.explore.tasks import _task_files, discover_explore_tasks
from dokimi.ingest.loader import discover_markdown
from dokimi.memory import MemoryStoreError, read_claude_memory
from dokimi.novelty.retest import load_runs
from dokimi.repos.errors import PrepError
from dokimi.repos.arms import Skill
from dokimi.repos.skill_dirs import only_empty_dirs
from dokimi.repos.workspace import Task, _install_skills
from dokimi.runner.run import DocLoadError, run_benchmark
from dokimi.skill_coverage import _module_code, check_files, load_checks
from dokimi.utils import InputError


@contextmanager
def unlistable(path: Path):
    """``path`` at mode 111 for the block: a process that is not root cannot list it."""
    path.chmod(0o111)
    try:
        try:
            os.listdir(path)
        except PermissionError:
            yield
        else:
            pytest.skip("this process lists a mode 111 directory (root)")
    finally:
        path.chmod(0o700)


def _write(root: Path, rel: str, text: str = "x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_markdown_ingest_refuses_a_directory_it_cannot_list(tmp_path: Path):
    _write(tmp_path, "a.md")
    _write(tmp_path, "sub/b.md")
    _write(tmp_path, "sub/notes.txt")
    (tmp_path / "sub" / "dir.md").mkdir()  # a directory is not a document
    assert [p.relative_to(tmp_path).as_posix() for p in discover_markdown(tmp_path)] == ["a.md", "sub/b.md"]
    with unlistable(tmp_path / "sub"), pytest.raises(PermissionError, match="sub"):
        discover_markdown(tmp_path)


def test_markdown_ingest_does_not_list_a_skipped_directory(tmp_path: Path):
    kept = _write(tmp_path, "a.md")
    _write(tmp_path, "node_modules/pkg/README.md")
    with unlistable(tmp_path / "node_modules"):
        assert discover_markdown(tmp_path) == [kept]


def test_markdown_discovery_of_a_directory_that_does_not_exist_finds_nothing(tmp_path: Path):
    assert discover_markdown(tmp_path / "docs") == []


def test_a_benchmark_run_names_a_docs_directory_it_cannot_list(tmp_path: Path):
    _write(tmp_path, "docs/sub/b.md")
    spec = _write(tmp_path, "spec.yaml", "weights: {}\nmetrics: {freshness_lite: {max_age_days: 1}, readability: {grade_max: 1}}\n")
    with unlistable(tmp_path / "docs" / "sub"), pytest.raises(DocLoadError, match="could not list .*sub"):
        run_benchmark(tmp_path, spec)


def test_explore_tasks_refuse_a_directory_they_cannot_list(tmp_path: Path):
    for rel, name in (("a.yaml", "a"), ("sub/b.yaml", "b")):
        _write(tmp_path, rel, f"id: {name}\nquery: where?\nreferences:\n  - path: x.py\n")
    _write(tmp_path, "sub/notes.txt")
    (tmp_path / "sub" / "dir.yaml").mkdir()  # a directory is not a task file
    assert [p.relative_to(tmp_path).as_posix() for p in _task_files(tmp_path)] == ["a.yaml", "sub/b.yaml"]
    assert [t.id for t in discover_explore_tasks(tmp_path)] == ["a", "b"]
    with unlistable(tmp_path / "sub"), pytest.raises(ValueError, match="cannot list the explore tasks under .*sub"):
        discover_explore_tasks(tmp_path)


def _task(root: Path, name: str, tests: dict[str, str]) -> None:
    for rel, body in {"task.toml": 'version = "1.0"\n', "instruction.md": "Do it.\n"}.items():
        _write(root / name, rel, body)
    (root / name / "tests").mkdir(exist_ok=True)
    for rel, body in tests.items():
        _write(root / name / "tests", rel, body)


def _coverage_root(tmp_path: Path) -> Path:
    root = tmp_path / "tasks"
    _task(root, "t1", {"test_a.py": "X = 1\n\ndef test_a():\n    assert X\n", "sub/helpers.py": "Y = 2\n",
                         "sub/notes.txt": "x\n"})
    return root


def test_skill_coverage_module_code_refuses_a_tests_directory_it_cannot_list(tmp_path: Path):
    tests = _coverage_root(tmp_path) / "t1" / "tests"
    assert _module_code(tests) == ["Y = 2", "X = 1"]
    with unlistable(tests / "sub"), pytest.raises(PermissionError, match="sub"):
        _module_code(tests)


def test_skill_coverage_checks_and_the_out_guard_both_refuse_a_directory_they_cannot_list(tmp_path: Path):
    root = _coverage_root(tmp_path)
    for rel, body in {"task.toml": 'version = "1.0"\n', "instruction.md": "Do it.\n"}.items():
        _write(root / "t2", rel, body)  # a task with no tests/ folder
    _write(root / "notes" / "tests", "test_n.py", "def test_n():\n    pass\n")  # not a task: its tests are not read
    assert len(check_files(root)) == 2 and [c.name for c in load_checks(root)] == ["test_a"]
    with unlistable(root / "t1" / "tests" / "sub"):
        with pytest.raises(PermissionError, match="sub"):
            check_files(root)
        with pytest.raises(PermissionError, match="sub"):
            load_checks(root)


def test_memory_lint_refuses_an_archive_directory_it_cannot_list(tmp_path: Path):
    _write(tmp_path, "a.md")
    _write(tmp_path, "archive/sub/b.md")
    _write(tmp_path, "archive/sub/notes.txt")
    (tmp_path / "archive" / "sub" / "dir.md").mkdir()  # a directory is not a memory file
    assert [e.path for e in read_claude_memory(tmp_path).entries if e.archived] == ["archive/sub/b.md"]
    with unlistable(tmp_path / "archive" / "sub"), pytest.raises(MemoryStoreError, match="cannot list the archive: .*sub"):
        read_claude_memory(tmp_path)


def test_novelty_retest_refuses_a_runs_directory_it_cannot_list(tmp_path: Path):
    doc = {"artifact": {"ref": "skill:/s/a"}}
    _write(tmp_path, "runs/sub/deep/novelty.json", json.dumps(doc))
    _write(tmp_path, "runs/other/novelty.json", json.dumps({"artifact": {"ref": "skill:/s/b"}}))
    assert sorted(load_runs(tmp_path / "runs")) == ["skill:/s/a", "skill:/s/b"]
    with unlistable(tmp_path / "runs" / "sub"), pytest.raises(InputError, match="cannot list .*sub"):
        load_runs(tmp_path / "runs")


def test_novelty_retest_keeps_its_message_for_a_listable_tree_with_no_artifact(tmp_path: Path):
    _write(tmp_path, "runs/sub/other.json")
    for runs in (tmp_path / "runs", tmp_path / "missing"):
        with pytest.raises(InputError, match="no novelty.json under"):
            load_runs(runs)


def test_readers_keep_the_platform_filename_matching_rglob_had(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """On Windows ``rglob("*.md")`` ignores case; the readers match with ``Path.match``, which does the same there."""
    windows_match = PureWindowsPath.match
    monkeypatch.setattr(PurePath, "match", lambda self, pattern: windows_match(PureWindowsPath(str(self)), pattern))
    doc = _write(tmp_path / "docs", "README.MD")
    assert discover_markdown(tmp_path / "docs") == [doc]
    task = _write(tmp_path / "tasks", "task.YAML")
    assert _task_files(tmp_path / "tasks") == [task]
    _write(tmp_path / "mem", "archive/topic.MD")
    assert [e.path for e in read_claude_memory(tmp_path / "mem").entries] == ["archive/topic.MD"]
    root = tmp_path / "cov"
    _task(root, "t1", {"test_a.PY": "def test_a():\n    pass\n"})
    assert [p.name for p in check_files(root)] == ["test_a.PY"]
    _write(tmp_path / "runs", "NOVELTY.JSON", json.dumps({"artifact": {"ref": "skill:/s/a"}}))
    assert list(load_runs(tmp_path / "runs")) == ["skill:/s/a"]


def test_a_workspace_skills_directory_with_content_under_a_directory_it_cannot_list_is_refused(tmp_path: Path):
    dest = tmp_path / "d"
    _write(dest, "e/f.md")
    assert only_empty_dirs(dest) is False and only_empty_dirs(tmp_path / "missing") is False
    (dest / "e" / "f.md").unlink()
    assert only_empty_dirs(dest) is True
    _write(dest, "e/f.md")
    with unlistable(dest / "e"), pytest.raises(PrepError, match="cannot list .*/e"):
        only_empty_dirs(dest)


def test_installing_a_skill_over_a_directory_it_cannot_list_is_a_prep_error_and_keeps_it(tmp_path: Path):
    _write(tmp_path, "eval/skills/lint/SKILL.md", "---\nname: lint\ndescription: d\n---\n")
    ws = tmp_path / "ws"
    kept = _write(ws, ".claude/skills/lint/references/a.md")
    skill = Skill("lint", tmp_path / "eval/skills/lint")
    with unlistable(ws / ".claude/skills/lint/references"), pytest.raises(PrepError, match="cannot list .*references"):
        _install_skills(ws, Task("t", {}, tmp_path), [skill])
    assert kept.read_text(encoding="utf-8") == "x\n"
