# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""CLI coverage for executable task runs."""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.cli import build_parser


def _fixture_task(tmp_path: Path) -> Path:
    task = tmp_path / "toy-task"
    task.mkdir()
    (task / "instruction.md").write_text("Do it.", encoding="utf-8")
    (task / "task.toml").write_text("[metadata]\ntags = [\"toy\"]\n", encoding="utf-8")
    # A task Harbor can run: without these every cell errors and `tasks run` exits 1.
    (task / "environment").mkdir()
    (task / "tests").mkdir()
    (task / "tests" / "test.sh").write_text("exit 0\n", encoding="utf-8")
    return task


def test_tasks_run_dry_run_writes_schema_artifact(tmp_path):
    _fixture_task(tmp_path)
    out = tmp_path / "runs.json"
    parser = build_parser()
    args = parser.parse_args([
        "tasks",
        "run",
        "--tasks",
        "toy-task",
        "--tasks-root",
        str(tmp_path),
        "--harnesses",
        "codex",
        "--baseline-harness",
        "claude-code",
        "--model",
        "m",
        "--out-json",
        str(out),
        "--dry-run",
    ])
    args.func(args)
    text = out.read_text(encoding="utf-8")
    assert '"schema_version": "task_runs.v1"' in text
    assert '"codex"' in text
    assert '"claude-code"' in text
    # report is written alongside the default out-json path
    assert out.with_suffix(".md").is_file()


def test_tasks_run_out_md_creates_missing_parent_dir(tmp_path):
    _fixture_task(tmp_path)
    out = tmp_path / "runs.json"
    # --out-md points into a directory that does not exist yet.
    md = tmp_path / "nested" / "report.md"
    parser = build_parser()
    args = parser.parse_args([
        "tasks", "run", "--tasks", "toy-task", "--tasks-root", str(tmp_path),
        "--harnesses", "codex", "--model", "m",
        "--out-json", str(out), "--out-md", str(md), "--dry-run",
    ])
    args.func(args)  # must not raise FileNotFoundError
    assert md.is_file()
    assert "# Executable task-run report" in md.read_text(encoding="utf-8")


def test_tasks_list_reads_the_given_root(tmp_path, capsys):
    _fixture_task(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["tasks", "list", "--tasks-root", str(tmp_path)])
    args.func(args)
    assert capsys.readouterr().out.splitlines() == ["toy-task\t?\ttoy"]


def test_tasks_list_without_root_exits_with_a_readable_message():
    parser = build_parser()
    args = parser.parse_args(["tasks", "list"])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "--tasks-root" in str(excinfo.value)


def test_tasks_run_all_without_root_exits_with_a_readable_message(tmp_path):
    parser = build_parser()
    args = parser.parse_args([
        "tasks", "run", "--all", "--harnesses", "codex", "--dry-run",
        "--out-json", str(tmp_path / "runs.json"),
    ])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "--tasks-root" in str(excinfo.value)


def test_tasks_run_named_task_without_root_exits_with_a_readable_message(tmp_path):
    parser = build_parser()
    args = parser.parse_args([
        "tasks", "run", "--tasks", "toy-task", "--harnesses", "codex", "--dry-run",
        "--out-json", str(tmp_path / "runs.json"),
    ])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "--tasks-root" in str(excinfo.value)


def test_tasks_run_with_a_missing_skill_path_exits_before_writing_anything(tmp_path):
    _fixture_task(tmp_path)
    out = tmp_path / "runs.json"
    args = build_parser().parse_args([
        "tasks", "run", "--tasks", "toy-task", "--tasks-root", str(tmp_path),
        "--harnesses", f"docker-claude-skill:{tmp_path / 'nope' / 'SKILL.md'}",
        "--out-json", str(out), "--dry-run",
    ])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "does not exist" in str(excinfo.value)
    assert not out.exists()


def test_tasks_list_rejects_a_root_that_is_not_a_directory(tmp_path):
    """A mistyped --tasks-root must not read as a suite with zero tasks."""
    parser = build_parser()
    args = parser.parse_args(["tasks", "list", "--tasks-root", str(tmp_path / "typo")])
    with pytest.raises(SystemExit) as excinfo:
        args.func(args)
    assert "--tasks-root is not a directory" in str(excinfo.value)


def test_tasks_list_prints_tasks_from_a_named_root(tmp_path, capsys):
    _fixture_task(tmp_path)
    parser = build_parser()
    args = parser.parse_args(["tasks", "list", "--tasks-root", str(tmp_path)])
    args.func(args)
    out = capsys.readouterr().out
    assert "toy-task" in out
    assert "toy" in out


class _RaisingHarness:
    def run(self, task, **_):
        raise RuntimeError("docker build failed")


def _run_tasks(tmp_path, monkeypatch, harness_for):
    from dokimi.harnesses import runner as runner_mod

    _fixture_task(tmp_path)
    real = runner_mod.create_harness
    monkeypatch.setattr(runner_mod, "create_harness",
                        lambda hid, **kw: harness_for(hid) or real(hid, **kw))
    args = build_parser().parse_args([
        "tasks", "run", "--tasks", "toy-task", "--tasks-root", str(tmp_path),
        "--harnesses", "codex", "--baseline-harness", "claude-code", "--model", "m",
        "--out-json", str(tmp_path / "runs.json"), "--dry-run",
    ])
    try:
        args.func(args)
    except SystemExit as exc:
        return exc.code
    return 0


def test_tasks_run_with_every_cell_errored_is_incomplete(tmp_path, monkeypatch, capsys):
    # "codex: 0/0 pass (n/a), 1 errored" was exit 0.
    code = _run_tasks(tmp_path, monkeypatch, lambda hid: _RaisingHarness())
    out, err = capsys.readouterr()
    assert code == 1
    assert "codex: 0/0 pass (n/a), 1 errored" in out
    assert "incomplete: every cell errored (0 scored, 2 errored of 2 cells)" in err
    assert (tmp_path / "runs.json").is_file()  # the artifact is still written


def test_tasks_run_with_some_cells_errored_keeps_exit_0_and_counts(tmp_path, monkeypatch, capsys):
    code = _run_tasks(tmp_path, monkeypatch, lambda hid: _RaisingHarness() if hid == "codex" else None)
    out, err = capsys.readouterr()
    assert code == 0
    assert "codex: 0/0 pass (n/a), 1 errored" in out and "incomplete" not in err
