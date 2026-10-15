# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo compare` over hand-written batch directories. No docker, no LLM."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.eval.binary_stats import mcnemar_test
from dokimi.repos import compare as compare_mod
from dokimi.repos.compare import CompareError, compare, format_compare, parse_selector, sign_flip_p


def _cell(batch: Path, task: str, arm: str, rep: int, *, success: bool | None = True, score: float | None = None,
          failure: bool = False, cost: float | None = 0.1, turns: int = 10, metrics: dict | None = None,
          model: str = "m-1") -> None:
    rd = batch / f"{task}__{arm.replace(':', '-')}__{model}__r{rep}"
    rd.mkdir(parents=True)
    score_task = score is not None
    grade = {"status": "grader_failure" if failure else "graded", "pass": None if score_task else success,
             "success": False if failure else (None if score_task else success),
             "score": None if failure else score, "score_only": score_task and not failure, "reasons": []}
    rec = {"task": task, "arm": arm, "model": model, "rep": rep, "score_task": score_task, "grade": grade,
           "agent": {"cost_usd": cost, "num_turns": turns, "timed_out": False, "is_error": False,
                     "over_budget": False, "network": "none"}}
    (rd / "meta.json").write_text("{}")
    (rd / "result.json").write_text(json.dumps(rec))
    (rd / "grade.json").write_text(json.dumps({"metrics": metrics or {}}))


def _row(summary, measure, treatment="guidance:raw", model="m-1"):
    rows = [r for r in summary["comparisons"]
            if r["measure"] == measure and r["treatment"] == treatment and r["model"] == model]
    assert len(rows) == 1, rows
    return rows[0]


@pytest.mark.parametrize("gained,lost,tied", [(4, 0, 3), (5, 2, 1), (3, 3, 0), (1, 0, 1), (7, 1, 10)])
def test_sign_flip_equals_mcnemar_exact_at_one_cell_per_arm(gained, lost, tied):
    deltas = [1.0] * gained + [-1.0] * lost + [0.0] * tied
    treatment = [True] * gained + [False] * lost + [True] * tied
    control = [False] * gained + [True] * lost + [True] * tied
    expected = mcnemar_test(treatment, control)["p_exact"]
    assert sign_flip_p(deltas) == pytest.approx(expected, abs=1e-4)


def test_sign_flip_has_no_p_when_every_task_tied():
    assert sign_flip_p([0.0, 0.0, 1e-12]) is None


def test_sign_flip_monte_carlo_tracks_the_exact_distribution(monkeypatch):
    deltas = [0.33, -0.67, 1.0, 0.33, 0.33, -0.33, 0.67, 0.33, 1.0, -0.33, 0.67, 0.33, 0.33, 0.67, -1.0, 0.33,
              0.67, 0.33]
    sampled = sign_flip_p(deltas)  # 18 non-zero deltas: above EXACT_MAX
    monkeypatch.setattr(compare_mod, "EXACT_MAX", 18)
    assert sampled == pytest.approx(sign_flip_p(deltas), abs=0.01)


def test_tasks_not_cells_are_the_unit(tmp_path):
    b = tmp_path / "b1"
    for rep, ok in enumerate((False, False, True), 1):
        _cell(b, "fix_a", "guidance:none", rep, success=ok)
    for rep in (1, 2, 3):
        _cell(b, "fix_a", "guidance:raw", rep, success=True)
    _cell(b, "fix_b", "guidance:none", 1, success=True)
    _cell(b, "fix_b", "guidance:raw", 1, failure=True)  # a grader failure is a failure, as in `repo report`
    s = compare([b], "guidance:none")
    gate = _row(s, "gate")
    assert gate["n_tasks"] == 2 and (gate["up"], gate["down"], gate["tied"]) == (1, 1, 0)
    by_task = {t["task"]: t for t in gate["tasks"]}
    assert by_task["fix_a"]["baseline"] == pytest.approx(1 / 3) and by_task["fix_a"]["treatment"] == 1.0
    assert by_task["fix_a"]["n_baseline"] == 3 and by_task["fix_b"]["delta"] == -1.0
    assert gate["delta"]["mean"] == pytest.approx((2 / 3 - 1) / 2, abs=1e-4)


def test_score_tasks_are_their_own_measure_and_a_grader_failure_scores_zero(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "review_x", "guidance:none", 1, score=0.5)
    _cell(b, "review_x", "guidance:none", 2, score=0.5)
    _cell(b, "review_x", "guidance:raw", 1, score=1.0)
    _cell(b, "review_x", "guidance:raw", 2, score=1.0, failure=True)
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    s = compare([b], "guidance:none")
    score = _row(s, "score")
    assert score["n_tasks"] == 1 and score["tasks"][0]["treatment"] == 0.5 and score["delta"]["mean"] == 0.0
    assert _row(s, "gate")["n_tasks"] == 1  # the gated task, not the score task


def test_unpaired_tasks_are_listed_not_compared(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    _cell(b, "fix_only_base", "guidance:none", 1)
    _cell(b, "fix_only_treat", "guidance:raw", 1)
    s = compare([b], "guidance:none")
    assert _row(s, "gate")["n_tasks"] == 1
    assert {(u["task"], u["missing_in"]) for u in s["unpaired"]} == {("fix_only_base", "treatment"),
                                                                    ("fix_only_treat", "baseline")}
    assert "unpaired" in format_compare(s)


def test_holm_runs_over_gate_and_score_comparisons_only(tmp_path):
    b = tmp_path / "b1"
    for i in range(6):
        _cell(b, f"fix_{i}", "guidance:none", 1, success=False, cost=0.1)
        _cell(b, f"fix_{i}", "guidance:raw", 1, success=True, cost=0.2)
        _cell(b, f"fix_{i}", "guidance:bridge", 1, success=i < 5, cost=0.2)
    s = compare([b], "guidance:none")
    raw, bridge = _row(s, "gate"), _row(s, "gate", treatment="guidance:bridge")
    assert raw["p"] == pytest.approx(2 / 64) and bridge["p"] == pytest.approx(2 / 32)
    assert raw["p_holm"] == pytest.approx(2 * 2 / 64) and bridge["p_holm"] == pytest.approx(2 / 32)
    cost = _row(s, "cost_usd")
    assert cost["p"] == pytest.approx(2 / 64) and cost["p_holm"] is None


def test_selector_takes_one_arm_from_one_batch(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _cell(old, "fix_a", "guidance:raw", 1, success=False)
    _cell(new, "fix_a", "guidance:raw", 1, success=True)
    s = compare([old, new], "old/guidance:raw", ["new/guidance:raw"])
    gate = _row(s, "gate", treatment="new/guidance:raw")
    assert gate["tasks"][0]["baseline"] == 0.0 and gate["tasks"][0]["treatment"] == 1.0


def test_the_baseline_arm_from_another_batch_is_a_treatment_by_default(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _cell(old, "fix_a", "guidance:raw", 1, success=False)
    _cell(new, "fix_a", "guidance:raw", 1, success=True)
    _cell(new, "fix_a", "guidance:none", 1, success=True)
    s = compare([old, new], "old/guidance:raw")
    assert s["treatments"] == ["guidance:none", "new/guidance:raw"]
    assert _row(s, "gate", treatment="new/guidance:raw")["tasks"][0]["treatment"] == 1.0
    with pytest.raises(CompareError, match="also matches cells of the baseline"):
        compare([old, new], "old/guidance:raw", ["guidance:raw"])


def test_metrics_read_grade_json_and_skip_cells_without_them(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, metrics={"ran_tests": False, "n_build_fail": 3})
    _cell(b, "fix_a", "guidance:none", 2, metrics={"ran_tests": True})
    _cell(b, "fix_a", "guidance:raw", 1, metrics={"ran_tests": True, "n_build_fail": 1})
    s = compare([b], "guidance:none", metrics=("ran_tests", "n_build_fail", "absent"))
    ran = _row(s, "metric:ran_tests")["tasks"][0]
    assert (ran["baseline"], ran["treatment"]) == (0.5, 1.0)
    fails = _row(s, "metric:n_build_fail")["tasks"][0]
    assert (fails["baseline"], fails["treatment"]) == (3.0, 1.0)
    assert not [r for r in s["comparisons"] if r["measure"] == "metric:absent"]


def test_models_are_compared_separately_and_can_be_selected(tmp_path):
    b = tmp_path / "b1"
    for model in ("m-1", "m-2"):
        _cell(b, "fix_a", "guidance:none", 1, model=model)
        _cell(b, "fix_a", "guidance:raw", 1, model=model)
    assert {r["model"] for r in compare([b], "guidance:none")["comparisons"]} == {"m-1", "m-2"}
    assert {r["model"] for r in compare([b], "guidance:none", models=["m-2"])["comparisons"]} == {"m-2"}


@pytest.mark.parametrize("baseline,treatments,message", [
    ("guidance:bridge", None, "no finished cell matches the baseline"),
    ("guidance:none", ["guidance:none"], "is the baseline"),
    ("guidance:none", ["guidance:bridge"], "no finished cell matches the treatment"),
    ("/guidance:none", None, "bad arm selector"),
])
def test_bad_arms_are_refused(tmp_path, baseline, treatments, message):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    with pytest.raises(CompareError, match=message):
        compare([b], baseline, treatments)


def test_batches_need_distinct_names(tmp_path):
    for parent in ("x", "y"):
        _cell(tmp_path / parent / "b1", "fix_a", "guidance:none", 1)
    with pytest.raises(CompareError, match="distinct names"):
        compare([tmp_path / "x" / "b1", tmp_path / "y" / "b1"], "guidance:none")


def test_parse_selector_keeps_the_arm_whole():
    assert parse_selector("guidance:raw") == (None, "guidance:raw")
    assert parse_selector("r1/guidance:raw") == ("r1", "guidance:raw")


def test_cli_prints_the_table_and_json(tmp_path, capsys):
    b = tmp_path / "b1"
    for arm in ("guidance:none", "guidance:raw", "guidance:bridge"):
        _cell(b, "fix_a", arm, 1, success=arm != "guidance:none")
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none",
                                      "--arm", "guidance:raw,guidance:bridge"])
    args.func(args)
    out = capsys.readouterr().out
    assert "guidance:raw vs guidance:none, model m-1" in out and "guidance:bridge vs guidance:none" in out
    assert "gate changed: fix_a +1.00" in out
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none", "--json"])
    args.func(args)
    summary = json.loads(capsys.readouterr().out)
    assert summary["treatments"] == ["guidance:bridge", "guidance:raw"]


def test_cli_exits_on_a_bad_baseline(tmp_path, capsys):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:raw"])
    with pytest.raises(SystemExit):
        args.func(args)
    assert "no finished cell matches the baseline" in capsys.readouterr().err
