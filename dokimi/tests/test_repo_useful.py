# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The first useful result of a run: the task's ``useful`` definition, the event that hits it, and its report/compare rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.repos.events import _apply_result, convert
from dokimi.repos.host import validate_tasks
from dokimi.repos.report import format_report, summarize
from dokimi.repos.useful import (
    first_useful,
    useful_arms_line,
    useful_counts,
    useful_line,
    useful_summary,
    useful_task_values,
)
from dokimi.repos.useful_spec import UsefulSpecError, parse_useful
from dokimi.repos.workspace import Task

from dokimi.repos.compare import compare, format_compare
from test_repo_compare import _cell, _row
from test_repo_run import _only_cell, _run, setup  # noqa: F401  # the run fixture, with its helpers

TEST_CMD = r"pytest tests/test_x\.py"


def _command(text: str, rc: int | None, t: float | None = None, **extra) -> dict:
    return {"t": t, "kind": "command", "text": text, "rc": rc, **extra}


def _edit(path: str, t: float | None = None, **extra) -> dict:
    return {"t": t, "kind": "edit", "path": path, **extra}


# -- the definition ----------------------------------------------------------------------------------------------


def test_a_task_without_the_key_names_no_definition():
    assert parse_useful(None) is None


def test_the_two_kinds_parse_to_their_pattern_and_globs():
    command = parse_useful({"command": TEST_CMD})
    assert command.kind == "command" and command.command.search("pytest tests/test_x.py -q")
    assert parse_useful({"edit": "src/a.py"}).globs == ("src/a.py",)
    assert parse_useful({"edit": ["src/a.py", "**/*.h"]}).globs == ("src/a.py", "**/*.h")


@pytest.mark.parametrize(
    "value,needle",
    [
        ({"command": ""}, "useful must be"),
        ({"command": "("}, "not a valid regex"),
        ({"edit": []}, "useful must be"),
        ({"edit": ["src/a.py", ""]}, "useful must be"),
        ({"edit": 3}, "useful must be"),
        ({"command": "pytest", "edit": "src/a.py"}, "useful must be"),  # one kind per task
        ("pytest", "useful must be"),
        ({"test": "pytest"}, "useful must be"),
    ],
)
def test_a_malformed_definition_is_refused(value, needle):
    with pytest.raises(UsefulSpecError, match=needle):
        parse_useful(value)


def test_validate_tasks_refuses_a_malformed_definition_before_a_run():
    with pytest.raises(UsefulSpecError, match="useful must be"):
        validate_tasks([Task("t", {"id": "t", "useful": {"edit": 3}}, Path("."))])


# -- the first hit -----------------------------------------------------------------------------------------------


def test_a_passing_matching_command_is_the_first_useful_result_counted_in_steps_and_seconds():
    events = [
        _command("ls", 0, t=1.0),
        _command("pytest tests/test_x.py", 1, t=2.0),  # runs, fails: not useful
        _edit("src/x.py", t=3.0),
        _command("pytest tests/test_x.py", None, t=4.0),  # no result came back: not useful
        _command("pytest tests/test_x.py", 0, t=5.0, denied=True),  # refused: never ran
        _command("pytest tests/test_x.py", 0, t=6.0),
        _command("pytest tests/test_x.py", 0, t=7.0),
    ]
    assert first_useful(parse_useful({"command": TEST_CMD}), events) == {
        "kind": "command",
        "reason": None,
        "issued_s": 6.0,
        "steps": 6,
    }


def test_the_first_edit_of_a_target_file_counts_steps_across_every_event_kind():
    events = [
        _command("cat README.md", 0, t=0.5),
        _edit("docs/x.md", t=1.0),
        _edit("src/other.py", t=1.5, denied=True),  # the hook refused the edit
        _edit("src/sub/lib.cpp", t=2.0),
        _edit("src/sub/lib.cpp", t=3.0),
    ]
    assert first_useful(parse_useful({"edit": ["src/**/*.cpp", "src/x.py"]}), events) == {
        "kind": "edit",
        "reason": None,
        "issued_s": 2.0,
        "steps": 4,
    }


def test_a_run_with_no_hit_is_never_and_names_no_steps():
    events = [_command(TEST_CMD, 1, t=1.0), _edit("docs/x.md", t=2.0)]
    assert first_useful(parse_useful({"command": TEST_CMD}), events) == {
        "kind": "command",
        "reason": "never",
        "issued_s": None,
        "steps": None,
    }


def test_a_run_whose_trace_was_not_read_is_unobserved_not_never():
    assert first_useful(parse_useful({"edit": "src/x.py"}), None) == {
        "kind": "edit",
        "reason": "unobserved",
        "issued_s": None,
        "steps": None,
    }


def test_a_task_without_a_definition_is_not_declared():
    assert first_useful(None, [_edit("src/x.py", t=1.0)]) == {
        "kind": None,
        "reason": "not_declared",
        "issued_s": None,
        "steps": None,
    }


def test_a_trace_without_timestamps_keeps_the_steps_and_says_untimed():
    events = [_edit("docs/x.md"), _edit("src/x.py")]
    assert first_useful(parse_useful({"edit": "src/x.py"}), events) == {
        "kind": "edit",
        "reason": "untimed",
        "issued_s": None,
        "steps": 2,
    }


def test_the_hit_is_timed_from_the_claude_stream_it_came_from():
    lines = [
        {
            "type": "assistant",
            "timestamp": "2026-10-31T10:00:00.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "a",
                        "name": "Bash",
                        "input": {"command": "pytest tests/test_x.py"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-31T10:00:03.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "a",
                        "is_error": True,
                        "content": "1 failed",
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-31T10:00:10.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "b",
                        "name": "Edit",
                        "input": {"file_path": "/ws/src/x.py"},
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "timestamp": "2026-10-31T10:00:42.000Z",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "c",
                        "name": "Bash",
                        "input": {"command": "pytest tests/test_x.py"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-31T10:00:45.000Z",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "c", "is_error": False, "content": "ok"}
                ]
            },
        },
    ]
    raw = "\n".join(json.dumps(x) for x in lines)
    events = convert("claude-stream-json", raw, "/ws").events
    assert first_useful(parse_useful({"command": TEST_CMD}), events) == {
        "kind": "command",
        "reason": None,
        "issued_s": 42.0,
        "steps": 3,
    }
    assert first_useful(parse_useful({"edit": "src/*.py"}), events) == {
        "kind": "edit",
        "reason": None,
        "issued_s": 10.0,
        "steps": 2,
    }


# -- report and compare rows -------------------------------------------------------------------------------------


def test_a_failed_edit_changed_nothing_so_the_next_successful_one_is_the_first_useful_result():
    failed = {"t": 4.0, "kind": "edit", "path": "src/foo.py"}
    _apply_result({"tool_use_id": "u1", "is_error": True, "content": "String to replace not found in file."},
                  {"u1": failed})
    ok = {"t": 5.0, "kind": "edit", "path": "src/foo.py"}
    _apply_result({"tool_use_id": "u2", "is_error": False, "content": "edited"}, {"u2": ok})
    u = parse_useful({"edit": "src/*.py"})
    assert first_useful(u, [failed]) == {"kind": "edit", "reason": "never", "issued_s": None, "steps": None}
    assert first_useful(u, [failed, ok]) == {"kind": "edit", "reason": None, "issued_s": 5.0, "steps": 2}


def _timed(batch: Path, task: str, arm: str, rep: int, useful: dict | None) -> None:
    """A finished gate cell whose ``result.json`` carries ``useful`` (left out for ``None``: an older batch)."""
    _cell(batch, task, arm, rep)
    path = batch / f"{task}__{arm.replace(':', '-')}__m-1__r{rep}" / "result.json"
    rec = json.loads(path.read_text())
    if useful is not None:
        rec["first_useful"] = useful
    path.write_text(json.dumps(rec))


def _hit(seconds: float, steps: int) -> dict:
    return {"kind": "command", "reason": None, "issued_s": seconds, "steps": steps}


def test_useful_summary_is_the_observed_cells_medians_and_why_the_rest_are_not():
    recs = [
        {"first_useful": _hit(10.0, 4)},
        {"first_useful": _hit(20.0, 6)},
        {"first_useful": {"kind": "command", "reason": "never", "issued_s": None, "steps": None}},
    ]
    assert useful_summary(recs) == {
        "kind": "command",
        "n": 3,
        "observed": 2,
        "median_s": 15.0,
        "median_steps": 5.0,
        "reasons": {"never": 1},
    }
    assert useful_summary([{}, {"first_useful": None}]) is None  # an older batch declares nothing
    assert useful_line(useful_summary(recs), 4) == [
        " " * 8 + "first useful (command): 2 of 3 observed, median 15.0 s, 5 steps, never 1"
    ]
    assert useful_line(None, 4) == []


def test_a_task_value_is_the_median_of_the_cells_that_observed_it():
    recs = [
        {"first_useful": _hit(10.0, 4)},
        {"first_useful": _hit(30.0, 8)},
        {"first_useful": _hit(50.0, 6)},
        {"first_useful": {"kind": "command", "reason": "never", "issued_s": None, "steps": None}},
    ]
    assert useful_task_values(recs) == {
        "first_useful_s": [30.0],
        "first_useful_steps": [6.0],
        "first_useful_rate": [0.75],
    }
    assert useful_task_values([{}]) == {
        "first_useful_s": [None],
        "first_useful_steps": [None],
        "first_useful_rate": [None],
    }
    assert useful_counts(recs) == {"observed": 3, "declared": 4}
    assert useful_counts([{}]) is None
    assert (
        useful_arms_line({"a": {"observed": 1, "declared": 2}, "b": None})
        == "first useful observed: a 1 of 2"
    )
    assert useful_arms_line({"a": None}) is None


def test_repo_report_shows_the_first_useful_line_under_its_row_only_for_a_declared_task(tmp_path):
    batch = tmp_path / "b"
    _timed(batch, "t1", "guidance:raw", 1, _hit(10.0, 4))
    _timed(batch, "t1", "guidance:raw", 2, _hit(20.0, 6))
    _timed(
        batch,
        "t1",
        "guidance:raw",
        3,
        {"kind": "command", "reason": "never", "issued_s": None, "steps": None},
    )
    _timed(batch, "t2", "guidance:raw", 1, None)
    summary = summarize(batch)
    rows = {r["task"]: r for r in summary["rows"]}
    assert rows["t1"]["first_useful"] == {
        "kind": "command",
        "n": 3,
        "observed": 2,
        "median_s": 15.0,
        "median_steps": 5.0,
        "reasons": {"never": 1},
    }
    assert "first_useful" not in rows["t2"]
    text = format_report(summary)
    assert "first useful (command): 2 of 3 observed, median 15.0 s, 5 steps, never 1" in text
    assert text.count("first useful") == 1


def test_repo_compare_pairs_the_first_useful_medians_per_task_and_says_so_for_a_partial_batch(
    tmp_path,
):
    batch = tmp_path / "b"
    base = {"t1": ((10.0, 30.0), (4, 6)), "t2": ((None, None), (None, None))}
    treat = {"t1": ((40.0, 50.0), (8, 10))}
    for task, (secs, steps) in base.items():
        for rep, (s, n) in enumerate(zip(secs, steps, strict=True), 1):
            _timed(batch, task, "guidance:none", rep, None if s is None else _hit(s, n))
    for rep, (s, n) in enumerate(zip(*treat["t1"], strict=True), 1):
        _timed(batch, "t1", "guidance:raw", rep, _hit(s, n))
    s = compare([batch], "guidance:none")
    row = _row(s, "first_useful_s")
    by_task = {t["task"]: t for t in row["tasks"]}
    assert by_task["t1"]["baseline"] == 20.0 and by_task["t1"]["treatment"] == 45.0
    assert row["delta"]["mean"] == 25.0
    assert _row(s, "first_useful_steps")["delta"]["mean"] == 4.0  # medians 5 and 9
    assert "t2" not in by_task  # no treatment cell of t2 observed one: t2 is left out, and said so
    assert {"task": "t2", "measure": None, "reason": "no cells in treatment"}.items() <= next(
        u for u in s["unpaired"] if u["task"] == "t2"
    ).items()


def test_an_older_batch_without_the_field_has_no_first_useful_row(tmp_path):
    batch = tmp_path / "old"
    for rep in (1, 2):
        _timed(batch, "t1", "guidance:none", rep, None)
        _timed(batch, "t1", "guidance:raw", rep, None)
    s = compare([batch], "guidance:none")
    assert not {r["measure"] for r in s["comparisons"]} & {"first_useful_s", "first_useful_steps"}
    assert not s["unpaired"]


def test_the_rate_says_how_many_declared_cells_observed_a_hit_where_the_medians_see_only_those_that_did(
    tmp_path,
):
    batch = tmp_path / "b"
    never = {"kind": "command", "reason": "never", "issued_s": None, "steps": None}
    _timed(batch, "t1", "guidance:none", 1, _hit(10.0, 4))
    _timed(batch, "t1", "guidance:none", 2, never)
    _timed(batch, "t1", "guidance:raw", 1, _hit(10.0, 4))
    _timed(batch, "t1", "guidance:raw", 2, _hit(12.0, 5))
    s = compare([batch], "guidance:none")
    by_task = {t["task"]: t for t in _row(s, "first_useful_rate")["tasks"]}
    assert by_task["t1"]["baseline"] == 0.5 and by_task["t1"]["treatment"] == 1.0
    assert _row(s, "first_useful_rate")["delta"]["mean"] == 0.5
    assert by_task["t1"]["baseline"] < 1.0  # the medians alone would read this arm as no slower
    assert _row(s, "first_useful_s")["delta"]["mean"] == 1.0  # medians 10 and 11
    assert s["first_useful"] == {
        "guidance:none": {"observed": 1, "declared": 2},
        "guidance:raw": {"observed": 2, "declared": 2},
    }
    assert "first useful observed: guidance:none 1 of 2, guidance:raw 2 of 2" in format_compare(s)


# -- the run itself ----------------------------------------------------------------------------------------------


def test_result_json_records_the_first_edit_of_a_declared_target(setup):  # noqa: F811
    task_json = setup["root"] / ".agent-eval/tasks/fix_add/task.json"
    task = json.loads(task_json.read_text())
    task["useful"] = {"edit": "src/calc.py"}
    task_json.write_text(json.dumps(task))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", source=str(setup["root"]))
    # the fake agent's one Edit, with no timestamps: a hit that is untimed, at step 1
    assert _only_cell(setup)["first_useful"] == {
        "kind": "edit",
        "reason": "untimed",
        "issued_s": None,
        "steps": 1,
    }


def test_result_json_says_a_definition_that_no_command_hits_is_never(setup, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FAKE_PROCESS", "1")  # a `bazel build` the fake agent runs and fails
    task_json = setup["root"] / ".agent-eval/tasks/fix_add/task.json"
    task = json.loads(task_json.read_text())
    task["useful"] = {"command": "bazel build"}
    task_json.write_text(json.dumps(task))
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", source=str(setup["root"]))
    assert _only_cell(setup)["first_useful"] == {
        "kind": "command",
        "reason": "never",
        "issued_s": None,
        "steps": None,
    }


def test_result_json_says_a_task_with_no_definition_is_not_declared(setup):  # noqa: F811
    _run(setup, "--arm", "guidance:raw", "--task", "fix_add", source=str(setup["root"]))
    assert _only_cell(setup)["first_useful"] == {
        "kind": None,
        "reason": "not_declared",
        "issued_s": None,
        "steps": None,
    }


def test_repo_run_refuses_a_malformed_definition_before_any_cell_runs(setup):  # noqa: F811
    task_json = setup["root"] / ".agent-eval/tasks/fix_add/task.json"
    task = json.loads(task_json.read_text())
    task["useful"] = {"edit": 3}
    task_json.write_text(json.dumps(task))
    with pytest.raises(SystemExit):
        _run(setup, "--arm", "guidance:raw", "--task", "fix_add", source=str(setup["root"]))
    assert not list(
        (setup["out"] / "b1").glob("fix_add__*")
    )  # refused by validate_tasks: no cell was made
