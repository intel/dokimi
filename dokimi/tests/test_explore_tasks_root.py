# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`explore list` and `explore run`: no task under the tasks root exits 2, not an empty list."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser

TASK = "id: t\nproduct: p\nquery: q\nreferences:\n  - path: a.py\n"


def _refusal(argv, capsys):
    args = build_parser().parse_args(argv)
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    return exc.value.code, capsys.readouterr()


@pytest.fixture
def bundled_absent(tmp_path, monkeypatch):
    import dokimi.explore.tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "EXPLORE_TASKS_ROOT", tmp_path / "absent")


@pytest.fixture
def bundled_empty(tmp_path, monkeypatch):
    import dokimi.explore.tasks as tasks_mod

    bundled = tmp_path / "bundled"
    bundled.mkdir()
    monkeypatch.setattr(tasks_mod, "EXPLORE_TASKS_ROOT", bundled)


@pytest.mark.parametrize("fixture,message", [
    ("bundled_absent", "No bundled explore tasks at "),
    ("bundled_empty", "No explore tasks in the bundled data/explore_tasks."),
])
@pytest.mark.parametrize("argv", [
    ["explore", "list"],
    ["explore", "run", "--all"],
])
def test_no_root_and_no_bundled_task_exits_2_naming_tasks_root(fixture, message, argv, request, capsys):
    request.getfixturevalue(fixture)
    code, err = _refusal(argv, capsys)
    assert code == 2
    assert message in err.err
    assert "--tasks-root" in err.err


@pytest.mark.parametrize("argv", [
    ["explore", "list"],
    ["explore", "run", "--all"],
])
def test_missing_tasks_root_exits_2_naming_it(tmp_path, capsys, argv):
    missing = tmp_path / "absent"
    code, err = _refusal([*argv, "--tasks-root", str(missing)], capsys)
    assert code == 2
    assert str(missing) in err.err
    assert "--tasks-root" in err.err


@pytest.mark.parametrize("argv", [
    ["explore", "list"],
    ["explore", "run", "--all"],
])
def test_tasks_root_with_no_yaml_exits_2(tmp_path, capsys, argv):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "notes.txt").write_text("not a task", encoding="utf-8")
    code, err = _refusal([*argv, "--tasks-root", str(empty)], capsys)
    assert code == 2
    assert f"No explore tasks under {empty}. Pass --tasks-root" in err.err


def test_run_by_id_with_no_bundled_task_exits_2(bundled_absent, capsys):
    code, err = _refusal(["explore", "run", "--tasks", "t"], capsys)
    assert code == 2
    assert "--tasks-root" in err.err


def test_run_with_no_selection_exits_2(tmp_path, capsys):
    (tmp_path / "t.yaml").write_text(TASK, encoding="utf-8")
    code, err = _refusal(["explore", "run", "--tasks-root", str(tmp_path)], capsys)
    assert code == 2
    assert "No tasks selected" in err.err


def test_list_with_the_bundled_tasks_still_works(capsys):
    # The default root is kept: a source checkout lists its own tasks, exit 0.
    args = build_parser().parse_args(["explore", "list"])
    args.func(args)
    assert "citation-localization-scoring\tdokimi\t" in capsys.readouterr().out


def test_list_with_an_explicit_root_still_works(tmp_path, capsys):
    (tmp_path / "t.yaml").write_text(TASK, encoding="utf-8")
    args = build_parser().parse_args(["explore", "list", "--tasks-root", str(tmp_path)])
    args.func(args)
    assert "t\tp\t1 ref(s)" in capsys.readouterr().out
