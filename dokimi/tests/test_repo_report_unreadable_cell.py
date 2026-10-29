# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo report` and `repo compare` exit 1 with a message, not a traceback, on a corrupt cell file."""

from __future__ import annotations

import json
import math

import pytest

from dokimi.cli import build_parser
from dokimi.repos.report import _SAFE_MAGNITUDE


def _batch(tmp_path, name: str, files: dict[str, bytes]):
    batch = tmp_path / name
    for rel, content in files.items():
        path = batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return batch


def _run(*argv: str) -> None:
    args = build_parser().parse_args(["repo", *argv])
    args.func(args)


def _no_constant(name: str) -> None:
    raise ValueError(f"{name} in the JSON output")


_CORRUPT = [
    pytest.param(b"{not json", id="not-json"),
    pytest.param(b"\xff\xfe", id="not-utf8"),
    pytest.param(b"[1, 2]", id="not-an-object"),
]


@pytest.mark.parametrize("content", _CORRUPT)
def test_report_exits_1_naming_a_corrupt_result_file(tmp_path, capsys, content):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": content})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert str(batch / "t__a__m__r1" / "result.json") in err
    assert "Traceback" not in err


@pytest.mark.parametrize("content", _CORRUPT)
def test_compare_exits_1_naming_a_corrupt_result_file(tmp_path, capsys, content):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": content})
    with pytest.raises(SystemExit) as exc:
        _run("compare", str(batch), "--baseline", "a")
    assert exc.value.code == 1
    assert str(batch / "t__a__m__r1" / "result.json") in capsys.readouterr().err


def test_report_exits_1_naming_a_corrupt_unsupported_file(tmp_path, capsys):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/unsupported.json": b"{not json"})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert str(batch / "t__a__m__r1" / "unsupported.json") in capsys.readouterr().err


@pytest.mark.parametrize("content", [b"{not json", b"{}", b'{"cells": 3}', b'{"cells": [1]}'])
def test_report_exits_1_naming_a_corrupt_plan_file(tmp_path, capsys, content):
    batch = _batch(tmp_path, "b1", {"_eval/plan.json": content})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert str(batch / "_eval" / "plan.json") in capsys.readouterr().err


def test_report_still_reads_an_empty_batch(tmp_path, capsys):
    batch = _batch(tmp_path, "b1", {"_eval/plan.json": json.dumps({"cells": []}).encode()})
    _run("report", str(batch), "--json")
    assert json.loads(capsys.readouterr().out)["rows"] == []


_NO_GRADE = [
    pytest.param(b"{}", id="empty"),
    pytest.param(b'{"grade": null}', id="null"),
    pytest.param(b'{"grade": []}', id="list"),
    pytest.param(b'{"grade": {"score_only": false}, "task": "t", "arm": "a", "model": "m"}', id="no-status"),
    pytest.param(b'{"grade": {"status": "ok"}}', id="status-only"),
]


@pytest.mark.parametrize("content", _NO_GRADE)
@pytest.mark.parametrize("argv", [("report",), ("compare", "--baseline", "a")], ids=["report", "compare"])
def test_a_result_file_without_a_usable_grade_exits_1(tmp_path, capsys, argv, content):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": content})
    with pytest.raises(SystemExit) as exc:
        _run(argv[0], str(batch), *argv[1:])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"{batch / 't__a__m__r1' / 'result.json'} has no usable `grade`" in err
    assert "Traceback" not in err


_GOOD = {"grade": {"status": "ok", "score_only": False, "success": True}, "task": "t", "arm": "a", "model": "m",
         "agent": {"timed_out": False, "is_error": False, "over_budget": False, "cost_usd": 0.1, "num_turns": 2}}

_BAD_FIELDS = [
    pytest.param("agent", None, id="missing-agent"),
    pytest.param("agent", [], id="agent-not-a-dict"),
    pytest.param("agent", {"timed_out": False}, id="agent-missing-keys"),
    pytest.param("task", None, id="missing-task"),
    pytest.param("arm", None, id="missing-arm"),
    pytest.param("model", None, id="missing-model"),
    pytest.param("task", 3, id="task-not-a-string"),
    pytest.param("grade", {"status": "ok", "success": True}, id="grade-without-score-only"),
    pytest.param("grade", {"status": "ok", "score_only": False}, id="grade-without-success"),
    pytest.param("agent", {**_GOOD["agent"], "cost_usd": "0.1"}, id="cost-a-string"),
    pytest.param("agent", {**_GOOD["agent"], "cost_usd": True}, id="cost-a-bool"),
    pytest.param("grade", {**_GOOD["grade"], "score": True}, id="grade-score-a-bool"),
    pytest.param("grade", {**_GOOD["grade"], "score": "1"}, id="grade-score-a-string"),
    pytest.param("grade", {**_GOOD["grade"], "score_only": "false", "score": 1.0}, id="grade-score-only-a-string"),
    pytest.param("score_max", True, id="score-max-a-bool"),
    pytest.param("agent", {**_GOOD["agent"], "num_turns": 10**400}, id="num-turns-overflows"),
    pytest.param("agent", {**_GOOD["agent"], "num_turns": "3"}, id="num-turns-a-string"),
    pytest.param("agent", {**_GOOD["agent"], "input_tokens": True}, id="input-tokens-a-bool"),
    pytest.param("skills", [], id="skills-not-a-mapping"),
    pytest.param("skills", {"measured": True, "expected_skill": "s"}, id="skills-no-skill-invoked"),
    pytest.param("skills", {"measured": True}, id="skills-measured-no-skill-invoked"),
    pytest.param("skills", {"measured": True, "expected_skill": "s", "skill_invoked": True},
                 id="skills-expected-no-selected-correct"),
    pytest.param("agent", {**_GOOD["agent"], "cost_usd": 10**308}, id="cost-at-10-to-the-308"),
    pytest.param("agent", {**_GOOD["agent"], "cost_usd": -1e308}, id="cost-at-minus-1e308"),
    pytest.param("agent", {**_GOOD["agent"], "cost_usd": math.nextafter(_SAFE_MAGNITUDE, math.inf)},
                 id="cost-just-above-the-bound"),
    pytest.param("agent", {**_GOOD["agent"], "cost_usd": -math.nextafter(_SAFE_MAGNITUDE, math.inf)},
                 id="cost-just-below-the-negative-bound"),
    pytest.param("agent", {**_GOOD["agent"], "output_tokens": "1"}, id="output-tokens-a-string"),
    pytest.param("agent", {**_GOOD["agent"], "cache_read_tokens": True}, id="cache-read-tokens-a-bool"),
    pytest.param("agent", {**_GOOD["agent"], "cache_write_tokens": "1"}, id="cache-write-tokens-a-string"),
    pytest.param("grade", {"status": "ok", "score_only": True, "success": True}, id="score-only-without-score"),
    pytest.param("skills", {"expected_skill": "s", "skill_invoked": True},
                 id="skills-without-measured-no-selected-correct"),
    pytest.param("agent", {**_GOOD["agent"], "num_turns": 10**308}, id="num-turns-at-10-to-the-308"),
]


@pytest.mark.parametrize("key, value", _BAD_FIELDS)
@pytest.mark.parametrize("argv", [("report",), ("compare", "--baseline", "a")], ids=["report", "compare"])
def test_a_result_file_without_usable_agent_task_arm_or_model_exits_1(tmp_path, capsys, argv, key, value):
    rec = {k: v for k, v in _GOOD.items() if k != key}
    if value is not None:
        rec[key] = value
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    with pytest.raises(SystemExit) as exc:
        _run(argv[0], str(batch), *argv[1:])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"{batch / 't__a__m__r1' / 'result.json'} has no usable `{key}`" in err
    assert "Traceback" not in err


_SKILLS_ACCEPTED = [
    pytest.param({"measured": False}, {}, id="unmeasured"),
    pytest.param({}, {}, id="empty"),
    pytest.param({"skill_invoked": True}, {"skill_invoked": 1}, id="no-measured-key"),
    pytest.param({"measured": True, "skill_invoked": True}, {"skill_invoked": 1}, id="measured-no-expected-skill"),
    pytest.param({"measured": True, "expected_skill": "s", "skill_invoked": True, "skill_selected_correct": True},
                 {"skill_expected": 1, "skill_selected_correct": 1}, id="measured-expected-skill"),
]


@pytest.mark.parametrize("skills, shown", _SKILLS_ACCEPTED)
def test_a_valid_skills_block_is_accepted_by_repo_report(tmp_path, capsys, skills, shown):
    rec = {**_GOOD, "skills": skills}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    _run("report", str(batch), "--json")
    row = json.loads(capsys.readouterr().out)["rows"][0]
    if skills and skills.get("measured", True):
        assert {k: row[k] for k in shown} == shown
        if "skill_expected" not in shown:
            assert "skill_expected" not in row
    else:
        assert "skill_measured" not in row


_NUMBERS_ACCEPTED = [pytest.param(_SAFE_MAGNITUDE, id="at-the-bound"), pytest.param(-_SAFE_MAGNITUDE, id="at-minus-the-bound")]


@pytest.mark.parametrize("cost", _NUMBERS_ACCEPTED)
def test_a_cost_at_the_bound_is_accepted(tmp_path, capsys, cost):
    rec = {**_GOOD, "agent": {**_GOOD["agent"], "cost_usd": cost}}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    _run("report", str(batch), "--json")
    assert json.loads(capsys.readouterr().out)["rows"]


def _three_task_batch(tmp_path, cost_a, cost_b):
    files = {}
    for task in ("t1", "t2", "t3"):
        for arm, cost in (("a", cost_a), ("b", cost_b)):
            rec = {**_GOOD, "task": task, "arm": arm, "agent": {**_GOOD["agent"], "cost_usd": cost}}
            files[f"{task}__{arm}__m__r1/result.json"] = json.dumps(rec).encode()
    return _batch(tmp_path, "b1", files)


@pytest.mark.parametrize("cost", [1e154, 1e300])
@pytest.mark.parametrize("cmd", [("report",), ("compare", "--baseline", "a")], ids=["report", "compare"])
def test_deltas_that_would_overflow_when_squared_are_rejected_with_a_message(tmp_path, capsys, cmd, cost):
    batch = _three_task_batch(tmp_path, cost, -cost)
    with pytest.raises(SystemExit) as exc:
        _run(cmd[0], str(batch), *cmd[1:])
    assert exc.value.code == 1
    assert "has no usable `agent`" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", [("report",), ("compare", "--baseline", "a")], ids=["report", "compare"])
def test_deltas_at_the_bound_compare_without_overflow(tmp_path, capsys, cmd):
    batch = _three_task_batch(tmp_path, _SAFE_MAGNITUDE, -_SAFE_MAGNITUDE)
    _run(cmd[0], str(batch), *cmd[1:], "--json")
    assert json.loads(capsys.readouterr().out, parse_constant=_no_constant)


def test_a_result_file_with_a_score_task_and_no_score_only_is_accepted(tmp_path, capsys):
    rec = {**_GOOD, "score_task": False, "grade": {"status": "ok", "score": 1.0, "success": True},
           "agent": {**_GOOD["agent"], "cost_usd": None}}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    _run("report", str(batch), "--json")
    assert json.loads(capsys.readouterr().out)["rows"][0]["cost_unknown"] == 1


def test_a_deeply_nested_result_file_exits_1_naming_the_file(tmp_path, capsys):
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": b"[" * 100000})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert str(batch / "t__a__m__r1" / "result.json") in err and "Traceback" not in err


def test_an_unreadable_result_file_exits_1_naming_the_file(tmp_path, capsys):
    batch = _batch(tmp_path, "b1", {})
    (batch / "t__a__m__r1" / "result.json").mkdir(parents=True)  # a directory where the file belongs
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"cannot read {batch / 't__a__m__r1' / 'result.json'}" in err and "Traceback" not in err


@pytest.mark.parametrize("key", ["timed_out", "is_error", "over_budget", "cost_usd", "num_turns"])
def test_a_result_file_missing_an_agent_key_exits_1(tmp_path, capsys, key):
    agent = {k: v for k, v in _GOOD["agent"].items() if k != key}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps({**_GOOD, "agent": agent}).encode()})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert "has no usable `agent`" in capsys.readouterr().err


def test_a_result_file_without_grade_status_exits_1(tmp_path, capsys):
    grade = {k: v for k, v in _GOOD["grade"].items() if k != "status"}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps({**_GOOD, "grade": grade}).encode()})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert "has no usable `grade`" in capsys.readouterr().err


@pytest.mark.parametrize("cost", ["NaN", "Infinity", "-Infinity", "1e999", "9" * 400])
@pytest.mark.parametrize("cmd", [("report",), ("compare", "--baseline", "a")], ids=["report", "compare"])
def test_a_result_file_with_a_non_finite_cost_exits_1(tmp_path, capsys, cmd, cost):
    text = json.dumps(_GOOD).replace("0.1", cost)
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": text.encode()})
    with pytest.raises(SystemExit) as exc:
        _run(cmd[0], str(batch), *cmd[1:])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "has no usable `agent`" in err and "Traceback" not in err


def test_a_score_task_result_file_without_grade_score_exits_1(tmp_path, capsys):
    rec = {**_GOOD, "score_task": True, "grade": {"status": "ok", "success": True}}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert "has no usable `grade`" in capsys.readouterr().err


def test_a_score_task_result_file_with_a_null_grade_score_is_accepted(tmp_path, capsys):
    rec = {**_GOOD, "score_task": True, "grade": {"status": "grader_failure", "score": None, "success": False}}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    _run("report", str(batch), "--json")
    assert json.loads(capsys.readouterr().out)["rows"]


@pytest.mark.parametrize("value", ["false", 0, None, []])
def test_a_result_file_with_a_non_bool_score_task_exits_1(tmp_path, capsys, value):
    rec = {**_GOOD, "score_task": value, "grade": {"status": "ok", "score": 1.0, "success": True}}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert "has no usable `score_task`" in capsys.readouterr().err


def test_a_score_task_result_file_without_grade_success_exits_1(tmp_path, capsys):
    rec = {**_GOOD, "score_task": True, "grade": {"status": "ok", "score": 1.0}}
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode()})
    with pytest.raises(SystemExit) as exc:
        _run("report", str(batch))
    assert exc.value.code == 1
    assert "has no usable `grade`" in capsys.readouterr().err


def _two_arm_batch(tmp_path, rec):
    return _batch(tmp_path, "b1", {"t__a__m__r1/result.json": json.dumps(rec).encode(),
                                   "t__b__m__r1/result.json": json.dumps({**rec, "arm": "b"}).encode()})


_CMDS = pytest.mark.parametrize("cmd", [("report",), ("compare", "--baseline", "a")], ids=["report", "compare"])


@_CMDS
def test_a_cell_shape_the_checks_miss_exits_1_naming_the_cell(tmp_path, capsys, monkeypatch, cmd):
    from dokimi.repos import report

    monkeypatch.setattr(report, "_check_cell", lambda path, rec: None)
    batch = _two_arm_batch(tmp_path, {**_GOOD, "grade": "x"})
    with pytest.raises(SystemExit) as exc:
        _run(cmd[0], str(batch), *cmd[1:])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "t__a__m__r1" in err and "result.json" in err and "unexpected shape" in err


@_CMDS
@pytest.mark.parametrize("exc", [KeyError("k"), TypeError("t"), AttributeError("a"), OverflowError("o"), ValueError("v")],
                         ids=lambda e: type(e).__name__)
def test_each_cell_shape_error_exits_1_naming_the_cell(tmp_path, capsys, monkeypatch, cmd, exc):
    from dokimi.repos import compare, report

    def boom(recs):
        raise exc

    monkeypatch.setattr(report, "process_means", boom)
    monkeypatch.setattr(compare, "_process_lists", boom)
    batch = _two_arm_batch(tmp_path, _GOOD)
    with pytest.raises(SystemExit) as raised:
        _run(cmd[0], str(batch), *cmd[1:])
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "t__a__m__r1" in err and f"unexpected shape ({type(exc).__name__}" in err


@pytest.mark.parametrize("target", ["pass_hat", "order_distance"])
def test_an_error_in_compare_statistics_is_not_reported_as_a_bad_cell(tmp_path, monkeypatch, target):
    from dokimi.repos import compare

    def boom(*args):
        raise KeyError("a defect, not a cell")

    monkeypatch.setattr(compare, target, boom)
    batch = _two_arm_batch(tmp_path, _GOOD)
    with pytest.raises(KeyError, match="a defect, not a cell"):
        _run("compare", str(batch), "--baseline", "a", "--pass-k", "1", "--path-stability")


@_CMDS
@pytest.mark.parametrize("score", ["NaN", "Infinity", "-Infinity", "1e200", pytest.param("9" * 400, id="400-digits")])
def test_a_stored_unmeasurable_score_is_read_as_a_grader_failure(tmp_path, capsys, cmd, score):
    rec = {**_GOOD, "score_task": True, "grade": {"status": "graded", "score_only": True, "success": None,
                                                  "score": 0.5, "reasons": []}}
    text = json.dumps(rec).replace("0.5", score)
    batch = _batch(tmp_path, "b1", {"t__a__m__r1/result.json": text.encode(),
                                    "t__b__m__r1/result.json": json.dumps({**rec, "arm": "b"}).encode()})
    _run(cmd[0], str(batch), *cmd[1:], "--json")
    out = json.loads(capsys.readouterr().out, parse_constant=_no_constant)
    if cmd[0] == "report":
        row = next(r for r in out["rows"] if r["arm"] == "a")
        assert row["grader_failures"] == 1 and row["score"] == 0.0
    else:
        row = next(r for r in out["comparisons"] if r["measure"] == "score")
        assert (row["baseline_mean"], row["treatment_mean"]) == (0.0, 0.5)



def _huge_in_t0(huge, files_for):
    """Three tasks, arms a and b; arm a's value in task t0 is ``huge``, every other value is the task index."""
    files = {}
    for i in range(3):
        for arm in ("a", "b"):
            value = "HUGE" if (i, arm) == (0, "a") else i
            for rel, rec in files_for(f"t{i}", arm, value).items():
                files[f"t{i}__{arm}__m__r1/{rel}"] = json.dumps(rec).replace('"HUGE"', huge).encode()
    return files


_HUGE = pytest.mark.parametrize("huge", ["1e200", pytest.param("9" * 400, id="400-digits")])


@_CMDS
@_HUGE
def test_a_process_counter_beyond_the_bound_is_left_out(tmp_path, capsys, cmd, huge):
    files = _huge_in_t0(huge, lambda task, arm, value: {"result.json": {
        **_GOOD, "task": task, "arm": arm, "process": {"counters": {"x": value}}}})
    _run(cmd[0], str(_batch(tmp_path, "b1", files)), *cmd[1:], "--json")
    out = json.loads(capsys.readouterr().out, parse_constant=_no_constant)
    if cmd[0] == "report":
        rows = {r["task"]: r for r in out["rows"] if r["arm"] == "a"}
        assert "counter:x" not in rows["t0"].get("process", {}) and rows["t1"]["process"]["counter:x"] == 1.0
    else:
        row = next(r for r in out["comparisons"] if r["measure"] == "counter:x")
        assert [t["task"] for t in row["tasks"]] == ["t1", "t2"]


@_HUGE
def test_a_grade_metric_beyond_the_bound_is_left_out(tmp_path, capsys, huge):
    files = _huge_in_t0(huge, lambda task, arm, value: {"result.json": {**_GOOD, "task": task, "arm": arm},
                                                         "grade.json": {"metrics": {"m1": value}}})
    files["t1__b__m__r1/grade.json"] = b"[" * 100000  # a grade.json too deep to read has no metrics
    _run("compare", str(_batch(tmp_path, "b1", files)), "--baseline", "a", "--metric", "m1", "--json")
    out = json.loads(capsys.readouterr().out, parse_constant=_no_constant)
    row = next(r for r in out["comparisons"] if r["measure"] == "metric:m1")
    assert [t["task"] for t in row["tasks"]] == ["t2"]
