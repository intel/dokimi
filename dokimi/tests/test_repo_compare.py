# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo compare` over hand-written batch directories. No docker, no LLM."""

from __future__ import annotations

import itertools
import json
import random
from fractions import Fraction
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.eval.binary_stats import mcnemar_test
from dokimi.repos import compare as compare_mod
from dokimi.repos.compare import CompareError, compare, format_compare, parse_selector, sign_flip_p


def _cell(batch: Path, task: str, arm: str, rep: int, *, success: bool | None = True, score: float | None = None,
          failure: bool = False, cost: float | None = 0.1, turns: int = 10, metrics: dict | None = None,
          model: str = "m-1", harness_version: str = "1.0", model_resolved: str | list[str] | None = None,
          score_max: float | None = 1.0) -> None:
    rd = batch / f"{task}__{arm.replace(':', '-')}__{model}__r{rep}"
    rd.mkdir(parents=True)
    score_task = score is not None
    grade = {"status": "grader_failure" if failure else "graded", "pass": None if score_task else success,
             "success": False if failure else (None if score_task else success),
             "score": None if failure else score, "score_only": score_task and not failure, "reasons": []}
    rec = {"task": task, "arm": arm, "model": model, "rep": rep, "score_task": score_task, "grade": grade,
           "harness_version": harness_version, "model_resolved": model_resolved,
           "score_max": score_max if score_task else None,
           "agent": {"cost_usd": cost, "num_turns": turns, "timed_out": False, "is_error": False,
                     "over_budget": False, "network": "none"}}
    (rd / "meta.json").write_text("{}")
    (rd / "result.json").write_text(json.dumps(rec))
    (rd / "grade.json").write_text(json.dumps({"metrics": metrics or {}}))


def _stamp(batch: Path, **stamp) -> None:
    (batch / "_eval").mkdir(parents=True, exist_ok=True)
    (batch / "_eval" / "batch.json").write_text(json.dumps(stamp))


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


def _exact_p(deltas):
    """Sign-flip p in exact arithmetic: the oracle for sign_flip_p."""
    d = [x for x in deltas if x != 0]
    observed = abs(sum(d))
    hits = sum(abs(sum(s * x for s, x in zip(signs, d, strict=True))) >= observed
               for signs in itertools.product((1, -1), repeat=len(d)))
    return hits / 2 ** len(d)


def test_sign_flip_matches_exact_arithmetic_on_rates_of_repeated_cells():
    # per-task rates over 1-4 reps: 1/3 and 2/3 are where rounded floats used to drift past the observed sum
    rng = random.Random(7)
    for _ in range(300):
        deltas = []
        for _ in range(rng.randint(1, 9)):
            n = rng.randint(1, 4)
            deltas.append(Fraction(rng.randint(0, n), n) - Fraction(rng.randint(0, n), n))
        expected = _exact_p(deltas) if any(deltas) else None
        assert sign_flip_p([float(x) for x in deltas]) == (None if expected is None else pytest.approx(expected))


def test_rates_of_three_reps_give_the_exact_p_end_to_end(tmp_path):
    # deltas +2/3, -1/3, -1/3, +1/3: the true p is 1.0; computing it from rates rounded to 6 places gave 0.875
    b = tmp_path / "b1"
    plan = {"fix_a": ((0, 0, 0), (1, 1, 0)), "fix_b": ((1, 0, 0), (0, 0, 0)), "fix_c": ((1, 0, 0), (0, 0, 0)),
            "fix_d": ((0, 0, 0), (1, 0, 0))}
    for task, (base, treat) in plan.items():
        for rep, ok in enumerate(base, 1):
            _cell(b, task, "guidance:none", rep, success=bool(ok))
        for rep, ok in enumerate(treat, 1):
            _cell(b, task, "guidance:raw", rep, success=bool(ok))
    gate = _row(compare([b], "guidance:none"), "gate")
    expected = _exact_p([Fraction(2, 3), Fraction(-1, 3), Fraction(-1, 3), Fraction(1, 3)])
    assert expected == 1.0 and gate["p"] == pytest.approx(expected)
    assert {t["task"]: t["delta"] for t in gate["tasks"]} == {"fix_a": 0.666667, "fix_b": -0.333333,
                                                             "fix_c": -0.333333, "fix_d": 0.333333}


def test_sign_flip_has_no_p_when_every_task_tied():
    assert sign_flip_p([0.0, 0.0, 1e-12]) is None


def test_sign_flip_monte_carlo_counts_the_observed_pattern_and_follows_its_seed():
    # 18 identical wins: no resample beats the observed sum by chance alone, so p is 1 / (N + 1), never 0
    assert compare_mod.EXACT_MAX == 16 and compare_mod.N_RESAMPLES == 20_000
    assert sign_flip_p([1.0] * 18) == pytest.approx(1 / 20_001, abs=1e-6)
    assert sign_flip_p([1.0] * 16) == pytest.approx(2 / 2 ** 16, abs=1e-6)  # 16 changed tasks: still exact
    d18 = [1.0] * 12 + [-1.0] * 6
    assert sign_flip_p(d18, seed=1) != sign_flip_p(d18, seed=2)
    assert sign_flip_p(d18, seed=1) == sign_flip_p(d18, seed=1)


def test_seed_reaches_the_bootstrap_and_the_sign_flip(tmp_path, monkeypatch):
    seen = []

    def spy(name, result):
        return lambda deltas, seed: seen.append((name, seed)) or result

    monkeypatch.setattr(compare_mod, "bootstrap_ci", spy("bootstrap", {"mean": 0.0, "lo": -1.0, "hi": 1.0}))
    monkeypatch.setattr(compare_mod, "mean_ci", spy("t", {"mean": 0.0, "lo": -1.0, "hi": 1.0}))
    monkeypatch.setattr(compare_mod, "sign_flip_p", spy("p", 1.0))
    b = tmp_path / "b1"
    for task in ("fix_a", "fix_b"):
        _cell(b, task, "guidance:none", 1, success=False)
        _cell(b, task, "guidance:raw", 1)
    compare([b], "guidance:none", seed=7)
    assert {name for name, _ in seen} == {"bootstrap", "t", "p"}
    assert {seed for _, seed in seen} == {7}


def test_a_small_but_real_delta_is_not_a_tie():
    assert sign_flip_p([1e-5, 1.0]) == 0.5


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


@pytest.mark.parametrize("base,treat,expected", [
    ((True, True, True), (True, True, True), "ceiling"),
    ((False, False, False), (False, False, False), "floor"),
    ((True, True, False), (True, True, True), "near_ceiling"),  # one task can move up
    ((True, True, True), (True, False, True), "near_ceiling"),  # none can move up; a loss still shows
    ((True, False, False), (True, True, True), None),
    ((False, True, False), (False, True, False), None),  # all tied, but not all at one end
])
def test_gate_headroom_flags_a_task_set_that_cannot_move(tmp_path, base, treat, expected):
    b = tmp_path / "b1"
    for i, (was, now) in enumerate(zip(base, treat, strict=True)):
        _cell(b, f"fix_{i}", "guidance:none", 1, success=was)
        _cell(b, f"fix_{i}", "guidance:raw", 1, success=now)
    s = compare([b], "guidance:none")
    gate = _row(s, "gate")
    assert gate["headroom"] == expected and _row(s, "cost_usd")["headroom"] is None
    text = format_compare(s)
    if expected == "ceiling":
        assert gate["p"] is None and gate["p_holm"] is None  # the flag is a note: p and Holm are unchanged
        assert "gate ceiling: both arms pass all 3 paired tasks; no headroom to show an effect" in text
    elif expected == "floor":
        assert "gate floor: both arms fail all 3 paired tasks" in text
    elif expected == "near_ceiling":
        assert "gate near ceiling: the baseline passes all but at most one of 3 paired tasks" in text
    else:
        assert "ceiling" not in text and "floor" not in text


def test_score_headroom_is_measured_against_score_max(tmp_path):
    b = tmp_path / "b1"
    for i in range(2):
        _cell(b, f"review_{i}", "guidance:none", 1, score=4.0, score_max=4.0)
        _cell(b, f"review_{i}", "guidance:raw", 1, score=4.0, score_max=4.0)
        _cell(b, f"old_{i}", "guidance:none", 1, score=1.0, score_max=None)  # a batch from before score_max
        _cell(b, f"old_{i}", "guidance:raw", 1, score=1.0, score_max=None)
    assert _row(compare([b], "guidance:none"), "score")["headroom"] is None  # old_* has no known top
    for i in range(2):
        for arm in ("guidance:none", "guidance:raw"):
            (b / f"old_{i}__{arm.replace(':', '-')}__m-1__r1/result.json").unlink()
    s = compare([b], "guidance:none")
    assert _row(s, "score")["headroom"] == "ceiling"
    assert "score ceiling: both arms reach score.max on all 2 paired tasks" in format_compare(s)
    c = tmp_path / "b2"
    for i in range(2):
        _cell(c, f"review_{i}", "guidance:none", 1, score=0.0)
        _cell(c, f"review_{i}", "guidance:raw", 1, score=0.0, failure=True)  # a grader failure scores 0
    assert _row(compare([c], "guidance:none"), "score")["headroom"] == "floor"


def test_score_headroom_unknown_when_pooling_legacy_and_current_cells(tmp_path):
    """Regression: when legacy cells (score_max=None) and current cells (score_max=1.0) appear for the same task,
    do not report ceiling if the legacy cells' maxima are unknown."""
    b = tmp_path / "b1"
    for i in range(2):
        # Current cells with known score_max
        _cell(b, f"task_{i}", "guidance:none", 1, score=1.0, score_max=1.0)
        _cell(b, f"task_{i}", "guidance:raw", 1, score=1.0, score_max=1.0)
        # Legacy cells from before score_max tracking, same task IDs
        _cell(b, f"task_{i}", "guidance:none", 2, score=1.0, score_max=None)
        _cell(b, f"task_{i}", "guidance:raw", 2, score=1.0, score_max=None)
    # Both arms reach 1.0, but legacy cells have unknown maxima: headroom should be None, not "ceiling"
    assert _row(compare([b], "guidance:none"), "score")["headroom"] is None


def test_unpaired_tasks_are_listed_not_compared(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    _cell(b, "fix_only_base", "guidance:none", 1)
    _cell(b, "fix_only_treat", "guidance:raw", 1)
    s = compare([b], "guidance:none")
    assert _row(s, "gate")["n_tasks"] == 1
    assert {(u["task"], u["reason"]) for u in s["unpaired"]} == {("fix_only_base", "no cells in treatment"),
                                                                ("fix_only_treat", "no cells in baseline")}
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


def test_holm_family_includes_the_score_comparisons(tmp_path):
    b = tmp_path / "b1"
    for i in range(6):
        _cell(b, f"fix_{i}", "guidance:none", 1, success=False)
        _cell(b, f"fix_{i}", "guidance:raw", 1, success=True)
        _cell(b, f"review_{i}", "guidance:none", 1, score=0.2)
        _cell(b, f"review_{i}", "guidance:raw", 1, score=0.8)
    s = compare([b], "guidance:none")
    for measure in ("gate", "score"):
        row = _row(s, measure)
        assert row["p"] == pytest.approx(2 / 64) and row["p_holm"] == pytest.approx(4 / 64)


def test_up_down_and_tied_count_each_task_once(tmp_path):
    b = tmp_path / "b1"
    for task, (was, now) in {"t1": (False, True), "t2": (False, True), "t3": (True, False), "t4": (True, True)}.items():
        _cell(b, task, "guidance:none", 1, success=was)
        _cell(b, task, "guidance:raw", 1, success=now)
    gate = _row(compare([b], "guidance:none"), "gate")
    assert (gate["up"], gate["down"], gate["tied"]) == (2, 1, 1)


def test_one_task_has_no_interval(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, success=False, turns=10)
    _cell(b, "fix_a", "guidance:raw", 1, success=True, turns=30)
    s = compare([b], "guidance:none")
    for measure in ("gate", "turns"):
        delta = _row(s, measure)["delta"]
        assert delta["mean"] is not None and delta["lo"] is None and delta["hi"] is None
    text = format_compare(s)
    assert "+1.000 -" in text and "+20.000 -" in text and "[+1.000, +1.000]" not in text


def test_continuous_measures_get_a_t_interval_and_shares_the_bootstrap(tmp_path):
    from dokimi.eval.stats import bootstrap_ci, mean_ci

    b = tmp_path / "b1"
    for task, extra in (("fix_a", 10), ("fix_b", 12), ("fix_c", 11)):
        _cell(b, task, "guidance:none", 1, success=False, turns=10)
        _cell(b, task, "guidance:raw", 1, success=True, turns=10 + extra)
    s = compare([b], "guidance:none")
    turns, gate = _row(s, "turns")["delta"], _row(s, "gate")["delta"]
    t = mean_ci([10, 12, 11])
    assert (turns["lo"], turns["hi"]) == (t["lo"], t["hi"])
    if t.get("method") == "student_t":  # SciPy present
        assert (turns["lo"], turns["hi"]) == pytest.approx((8.52, 13.48), abs=0.01)
    boot = bootstrap_ci([1.0, 1.0, 1.0])
    assert (gate["lo"], gate["hi"]) == (boot["lo"], boot["hi"])


def test_two_selectors_of_the_same_cells_are_refused(tmp_path):
    b = tmp_path / "t6d"
    _cell(b, "fix_a", "A", 1, success=False)
    _cell(b, "fix_a", "B", 1)
    with pytest.raises(CompareError, match="'B' and 't6d/B' select the same cells"):
        compare([b], "A", ["B", "t6d/B"])


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
    s = compare([b], "guidance:none", metrics=("ran_tests", "n_build_fail"))
    ran = _row(s, "metric:ran_tests")["tasks"][0]
    assert (ran["baseline"], ran["treatment"]) == (0.5, 1.0)
    fails = _row(s, "metric:n_build_fail")["tasks"][0]
    assert (fails["baseline"], fails["treatment"]) == (3.0, 1.0)
    # the cells behind each value: 2 report ran_tests, 1 of the 2 baseline cells reports n_build_fail
    assert (ran["n_baseline"], fails["n_baseline"], fails["n_treatment"]) == (2, 1, 1)


def test_a_metric_no_cell_reports_is_refused(tmp_path, capsys):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, metrics={"hits": 1, "label": "x"})
    _cell(b, "fix_a", "guidance:raw", 1, metrics={"hits": 2})
    with pytest.raises(CompareError, match="--metric typo"):
        compare([b], "guidance:none", metrics=("hits", "typo"))
    with pytest.raises(CompareError, match="--metric label"):  # reported, but not a number
        compare([b], "guidance:none", metrics=("label",))
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none", "--metric", "typo"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "--metric typo" in capsys.readouterr().err


def test_a_task_lacking_a_metric_is_listed_as_unpaired_for_that_metric(tmp_path):
    b = tmp_path / "b1"
    for task in ("fix_a", "fix_b", "fix_c"):
        _cell(b, task, "guidance:none", 1, metrics={"hits": 1} if task != "fix_c" else {})
        _cell(b, task, "guidance:raw", 1, metrics={"hits": 2})
    s = compare([b], "guidance:none", metrics=("hits",))
    assert _row(s, "metric:hits")["n_tasks"] == 2 and _row(s, "gate")["n_tasks"] == 3
    assert s["unpaired"] == [{"treatment": "guidance:raw", "model": "m-1", "task": "fix_c",
                              "measure": "metric:hits", "reason": "no metric:hits in baseline"}]
    assert "fix_c (m-1, no metric:hits in baseline)" in format_compare(s)


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


def test_a_task_whose_kind_differs_between_arms_is_left_out_of_gate_and_score(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _cell(old, "task_x", "guidance:raw", 1, success=True)  # gated in one eval tree
    _cell(new, "task_x", "guidance:raw", 1, score=0.5)  # score-only in the other
    _cell(old, "fix_a", "guidance:raw", 1, success=False)
    _cell(new, "fix_a", "guidance:raw", 1, success=True)
    s = compare([old, new], "old/guidance:raw", ["new/guidance:raw"])
    assert [t["task"] for t in _row(s, "gate", treatment="new/guidance:raw")["tasks"]] == ["fix_a"]
    assert not [r for r in s["comparisons"] if r["measure"] == "score"]
    assert len(_row(s, "turns", treatment="new/guidance:raw")["tasks"]) == 2  # process measures still pair
    assert [(u["task"], u["reason"]) for u in s["unpaired"]] == [("task_x", "gate task in baseline, score in treatment")]


def test_a_task_of_another_kind_in_every_arm_still_says_so(tmp_path):
    # Every task differs in kind: no gate or score row at all, so the output must name what was left out.
    k1, k2 = tmp_path / "k1", tmp_path / "k2"
    for task in ("t1", "t2", "t3"):
        _cell(k1, task, "A", 1, success=True)
        _cell(k2, task, "B", 1, score=0.5)
    s = compare([k1, k2], "k1/A", ["B"])
    assert {r["measure"] for r in s["comparisons"]} == {"cost_usd", "turns"}
    assert [u["task"] for u in s["unpaired"]] == ["t1", "t2", "t3"]
    assert "t1 (m-1, gate task in baseline, score in treatment)" in format_compare(s)


def test_a_model_with_no_compared_cells_is_refused(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    with pytest.raises(CompareError, match="no compared cells for --model m-typo"):
        compare([b], "guidance:none", models=["m-1", "m-typo"])


def test_unreadable_grade_json_leaves_the_metric_out(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, metrics={"ran_tests": True})
    _cell(b, "fix_a", "guidance:none", 2)
    _cell(b, "fix_a", "guidance:raw", 1, metrics={"ran_tests": False})
    _cell(b, "fix_a", "guidance:raw", 2)
    (b / "fix_a__guidance-none__m-1__r2" / "grade.json").write_text("{not json")
    (b / "fix_a__guidance-raw__m-1__r2" / "grade.json").write_text(json.dumps({"metrics": ["x"]}))
    pair = _row(compare([b], "guidance:none", metrics=("ran_tests",)), "metric:ran_tests")["tasks"][0]
    assert (pair["baseline"], pair["treatment"]) == (1.0, 0.0)  # the unreadable cells count for neither


def test_missing_cells_are_counted_and_left_out(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    (b / "fix_a__guidance-raw__m-1__r2").mkdir()
    (b / "fix_a__guidance-raw__m-1__r2" / "meta.json").write_text("{}")  # started, never finished
    s = compare([b], "guidance:none")
    assert s["missing"] == ["b1/fix_a__guidance-raw__m-1__r2"] and _row(s, "gate")["tasks"][0]["n_treatment"] == 1
    assert "1 cells missing" in format_compare(s)


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


def test_cli_passes_model_and_metric_and_refuses_a_missing_dir(tmp_path, capsys):
    b = tmp_path / "b1"
    for model in ("m-1", "m-2"):
        _cell(b, "fix_a", "guidance:none", 1, model=model, metrics={"n_build_fail": 2})
        _cell(b, "fix_a", "guidance:raw", 1, model=model, metrics={"n_build_fail": 0})
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none", "--model", "m-2",
                                      "--metric", "n_build_fail", "--json"])
    args.func(args)
    rows = json.loads(capsys.readouterr().out)["comparisons"]
    assert {r["model"] for r in rows} == {"m-2"}
    assert [r["delta"]["mean"] for r in rows if r["measure"] == "metric:n_build_fail"] == [-2.0]
    args = build_parser().parse_args(["repo", "compare", str(tmp_path / "nope"), "--baseline", "guidance:none"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "not a directory" in capsys.readouterr().err


def test_cli_exits_on_a_bad_baseline(tmp_path, capsys):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:raw"])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "no finished cell matches the baseline" in capsys.readouterr().err


def test_a_repeated_treatment_or_metric_is_one_comparison(tmp_path):
    b = tmp_path / "b1"
    for i in range(6):
        _cell(b, f"fix_{i}", "guidance:none", 1, success=False, metrics={"m": 0})
        _cell(b, f"fix_{i}", "guidance:raw", 1, success=i < 5, metrics={"m": 1})
    once = compare([b], "guidance:none", ["guidance:raw"], metrics=("m",))
    twice = compare([b], "guidance:none", ["guidance:raw", "guidance:raw"], metrics=("m", "m"))
    assert twice["treatments"] == ["guidance:raw"]
    assert twice["comparisons"] == once["comparisons"]
    assert _row(twice, "gate")["p_holm"] == _row(twice, "gate")["p"]  # a family of one, not two


@pytest.mark.parametrize("spec", ["", ","])
def test_an_empty_arm_list_is_refused_not_an_empty_comparison(tmp_path, capsys, spec):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    with pytest.raises(CompareError, match="no treatment arms"):
        compare([b], "guidance:none", [])
    args = build_parser().parse_args(["repo", "compare", str(b), "--baseline", "guidance:none", "--arm", spec])
    with pytest.raises(SystemExit) as exc:
        args.func(args)
    assert exc.value.code == 1 and "no treatment arms" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_values_are_not_measurements(tmp_path, bad):
    b = tmp_path / "b1"
    for i in range(4):
        _cell(b, f"fix_{i}", "guidance:none", 1, cost=0.1, metrics={"m": bad})
        _cell(b, f"fix_{i}", "guidance:raw", 1, cost=bad if i == 0 else 0.2, metrics={"m": 1})
    s = compare([b], "guidance:none", metrics=("m",))
    assert not [r for r in s["comparisons"] if r["measure"] == "metric:m"]  # no task has a finite value in both arms
    cost = _row(s, "cost_usd")
    assert [t["task"] for t in cost["tasks"]] == ["fix_1", "fix_2", "fix_3"]  # fix_0's cost is not a number
    assert cost["up"] + cost["down"] + cost["tied"] == cost["n_tasks"] == 3
    json.dumps(s, allow_nan=False)


def test_a_batch_given_as_dot_keeps_its_directory_name(tmp_path, monkeypatch):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, success=False)
    _cell(b, "fix_a", "guidance:raw", 1)
    monkeypatch.chdir(b)
    s = compare([Path(".")], "b1/guidance:none", ["b1/guidance:raw"])
    assert _row(s, "gate", treatment="b1/guidance:raw")["delta"]["mean"] == 1.0


def test_a_model_only_one_arm_ran_is_listed_as_unpaired(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, model="m-A")
    _cell(b, "fix_a", "guidance:raw", 1, model="m-B")
    s = compare([b], "guidance:none")
    assert s["comparisons"] == []
    assert s["unpaired"] == [
        {"treatment": "guidance:raw", "model": "m-A", "task": "fix_a", "measure": None,
         "reason": "no cells in treatment"},
        {"treatment": "guidance:raw", "model": "m-B", "task": "fix_a", "measure": None,
         "reason": "no cells in baseline"}]
    assert "fix_a (m-A, no cells in treatment)" in format_compare(s)


def test_each_selector_names_its_batches_and_a_bare_arm_over_several_warns(tmp_path):
    b1, b2 = tmp_path / "b1", tmp_path / "b2"
    for b in (b1, b2):
        _stamp(b, tree="repo@abc", eval_dir="e" * 64, image="dokimi/x:abc")
        _cell(b, "fix_a", "guidance:none", 1)
        _cell(b, "fix_a", "guidance:raw", 1)
    s = compare([b1, b2], "b1/guidance:none", ["guidance:raw"])
    assert s["sources"] == {"b1/guidance:none": ["b1"], "guidance:raw": ["b1", "b2"]}
    assert s["warnings"] == ["guidance:raw pools cells of 2 batches (b1, b2); name one as BATCH/ARM"]
    assert _row(s, "gate")["tasks"][0]["n_treatment"] == 2
    text = format_compare(s)
    assert "  guidance:raw: b1, b2" in text and "batch b1: tree repo@abc, eval_dir eeeeeeeeeeee" in text
    assert "warning: guidance:raw pools cells of 2 batches" in text
    assert compare([b1, b2], "b1/guidance:none", ["b1/guidance:raw"])["warnings"] == []


@pytest.mark.parametrize("key", ["eval_dir", "image"])
def test_arms_from_differently_stamped_batches_warn(tmp_path, key):
    old, new = tmp_path / "old", tmp_path / "new"
    base = {"tree": "repo@abc", "eval_dir": "e1", "image": "img:1"}
    _stamp(old, **base)
    _stamp(new, **{**base, "tree": "repo@def", key: "changed"})
    _cell(old, "fix_a", "guidance:raw", 1)
    _cell(new, "fix_a", "guidance:raw", 1)
    s = compare([old, new], "old/guidance:raw", ["new/guidance:raw"])
    assert s["warnings"] == [f"new/guidance:raw vs old/guidance:raw: the batches ran from different {key} "
                             f"({', '.join(sorted([base[key], 'changed']))})"]


def test_another_tree_alone_is_not_a_mismatch(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _stamp(old, tree="repo@abc", eval_dir="e1", image="img:1")
    _stamp(new, tree="repo@def", eval_dir="e1", image="img:1")  # another revision of the guidance: the treatment
    _cell(old, "fix_a", "guidance:raw", 1)
    _cell(new, "fix_a", "guidance:raw", 1)
    s = compare([old, new], "old/guidance:raw", ["new/guidance:raw"])
    assert s["warnings"] == [] and s["stamps"]["new"]["tree"] == "repo@def"


def test_an_unstamped_batch_beside_a_stamped_one_is_named(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _stamp(old, tree="r1", eval_dir="aaa", image="img1")
    _cell(old, "fix_a", "guidance:raw", 1)
    _cell(new, "fix_a", "guidance:raw", 1)
    s = compare([old, new], "old/guidance:raw", ["new/guidance:raw"])
    assert s["warnings"] == ["batch new has no stamp (_eval/batch.json); its eval_dir, image and graders are not compared"]


def test_images_built_from_each_tree_follow_the_tree(tmp_path):
    old, new = tmp_path / "t4c1", tmp_path / "t4c2"
    _stamp(old, tree="repo@111111111111aaaa", eval_dir="e1", image="dokimi/x:111111111111")
    _stamp(new, tree="repo@222222222222bbbb", eval_dir="e1", image="dokimi/x:222222222222")
    for b in (old, new):
        _cell(b, "fix_a", "A", 1)
    assert compare([old, new], "t4c1/A", ["t4c2/A"])["warnings"] == []
    _stamp(new, tree="/work/tree", eval_dir="e1", image="dokimi/x:worktree-0123456789ab")  # a working tree
    assert compare([old, new], "t4c1/A", ["t4c2/A"])["warnings"] == []
    _stamp(new, tree="repo@222222222222bbbb", eval_dir="e1", image="my/own:image")  # an --image of its own
    assert compare([old, new], "t4c1/A", ["t4c2/A"])["warnings"] == [
        "t4c2/A vs t4c1/A: the batches ran from different image (dokimi/x:111111111111, my/own:image)"]


def test_cells_on_different_harness_versions_warn(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, harness_version="2.1.286")
    _cell(b, "fix_a", "guidance:raw", 1, harness_version="2.1.296")
    s = compare([b], "guidance:none")
    assert s["warnings"] == ["guidance:raw vs guidance:none: cells ran on different harness versions "
                             "(2.1.286, 2.1.296)"]
    assert s["stamps"] == {"b1": {"harness_versions": ["2.1.286", "2.1.296"],  # no batch.json: no pin keys
                                  "model_resolved": []}}


def test_arms_answered_by_different_models_warn(tmp_path):
    b = tmp_path / "b1"
    _cell(b, "fix_a", "guidance:none", 1, model_resolved="m-1")
    _cell(b, "fix_a", "guidance:raw", 1, model_resolved="m-0")
    _cell(b, "fix_a", "guidance:skill", 1)  # an old cell, or a text trace: nothing to compare
    s = compare([b], "guidance:none")
    assert s["warnings"] == ["guidance:raw vs guidance:none: model m-1 was answered by m-1 in baseline, m-0 in "
                             "treatment",
                             "cells run with model m-1 were answered by m-0; check the harness's model aliases"]
    assert s["stamps"]["b1"]["model_resolved"] == ["m-0", "m-1"]
    assert "warning: cells run with model m-1 were answered by m-0" in format_compare(s)


def test_model_warnings_follow_the_selected_model(tmp_path):
    b = tmp_path / "b1"
    for task in ("fix_a", "fix_b"):
        _cell(b, task, "guidance:none", 1, model="m-2", model_resolved="m-2")
        _cell(b, task, "guidance:raw", 1, model="m-2", model_resolved="m-2")
        _cell(b, task, "guidance:none", 1, model="m-1", model_resolved="m-1")
        _cell(b, task, "guidance:raw", 1, model="m-1", model_resolved="m-0")
    assert compare([b], "guidance:none", models=["m-2"])["warnings"] == []
    assert len(compare([b], "guidance:none", models=["m-1"])["warnings"]) == 2


def test_model_matches_equivalence_does_not_warn(tmp_path):
    """Equivalent model IDs (Bedrock provider-prefix variants) do not trigger a warning."""
    b = tmp_path / "b1"
    # Request: Bedrock-prefixed, baseline resolve: shorter form, treatment resolve: base form without version
    _cell(b, "fix_a", "guidance:none", 1, model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
          model_resolved="anthropic.claude-haiku-4-5-20251001-v1:0")
    _cell(b, "fix_a", "guidance:raw", 1, model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
          model_resolved="claude-haiku-4-5-20251001")
    s = compare([b], "guidance:none")
    # No warning about different resolved models—they are equivalent under model_matches()
    assert [w for w in s["warnings"]
            if "was answered by" in w and "baseline" in w and "treatment" in w] == []
    # But cells mismatch still warns (the actual mismatch, not the compare mismatch)
    assert s["stamps"]["b1"]["model_resolved"] == ["anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5-20251001"]


def test_mixed_spellings_across_repetitions_do_not_warn(tmp_path):
    b = tmp_path / "b1"
    m = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    _cell(b, "fix_a", "guidance:none", 1, model=m, model_resolved=m)
    _cell(b, "fix_a", "guidance:none", 2, model=m, model_resolved="claude-haiku-4-5-20251001")
    _cell(b, "fix_a", "guidance:raw", 1, model=m, model_resolved="claude-haiku-4-5-20251001")
    _cell(b, "fix_a", "guidance:raw", 2, model=m, model_resolved="claude-haiku-4-5-20251001")
    assert [w for w in compare([b], "guidance:none")["warnings"] if "was answered by" in w] == []


def test_compare_models_equivalent_pins_symmetric_coverage(tmp_path):
    """Test that _models_equivalent checks both directions (baseline→treatment and treatment→baseline).

    A baseline with resolved model 'a' and treatment with only 'b' (where b matches a) should pass,
    but also: baseline with just 'a' and treatment with both 'a' and 'b' should still pass.
    Without the symmetric check, the second case would not raise a warning when it should identify
    that treatment has an extra model variant that baseline never produced.
    """
    b = tmp_path / "b1"
    m = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # Baseline: full Bedrock-prefixed model
    _cell(b, "fix_a", "guidance:none", 1, model=m, model_resolved="us.anthropic.claude-haiku-4-5-20251001-v1:0")
    # Treatment: shorter form (treated as equivalent by model_matches)
    _cell(b, "fix_a", "guidance:raw", 1, model=m, model_resolved="claude-haiku-4-5-20251001")
    # No warning because both forms are equivalent
    assert [w for w in compare([b], "guidance:none")["warnings"] if "was answered by" in w] == []
    # Baseline {m, short}, treatment {short, other}: `other` has no baseline match, so it must warn
    b2 = tmp_path / "b2"
    _cell(b2, "fix_a", "guidance:none", 1, model=m, model_resolved=m)
    _cell(b2, "fix_a", "guidance:none", 2, model=m, model_resolved="claude-haiku-4-5-20251001")
    _cell(b2, "fix_a", "guidance:raw", 1, model=m, model_resolved="claude-haiku-4-5-20251001")
    _cell(b2, "fix_a", "guidance:raw", 2, model=m, model_resolved="claude-opus-4-1")
    assert [w for w in compare([b2], "guidance:none")["warnings"] if "was answered by" in w]


def test_a_snapshot_only_the_baseline_got_warns(tmp_path):
    b = tmp_path / "b1"
    m = "claude-3-5-sonnet"
    _cell(b, "fix_a", "guidance:none", 1, model=m, model_resolved=f"{m}-20240620")
    _cell(b, "fix_a", "guidance:none", 2, model=m, model_resolved=f"{m}-20241022")
    _cell(b, "fix_a", "guidance:raw", 1, model=m, model_resolved=f"{m}-20241022")
    _cell(b, "fix_a", "guidance:raw", 2, model=m, model_resolved=f"{m}-20241022")
    assert compare([b], "guidance:none")["warnings"] == [
        f"guidance:raw vs guidance:none: model {m} was answered by {m}-20240620, {m}-20241022 in baseline, "
        f"{m}-20241022 in treatment"]

def test_an_unreadable_batch_stamp_is_no_stamp(tmp_path):
    b = tmp_path / "b1"
    (b / "_eval").mkdir(parents=True)
    (b / "_eval" / "batch.json").write_text("[1, 2]")
    _cell(b, "fix_a", "guidance:none", 1)
    _cell(b, "fix_a", "guidance:raw", 1)
    assert compare([b], "guidance:none")["stamps"]["b1"] == {"harness_versions": ["1.0"], "model_resolved": []}


def test_arms_from_batches_with_different_built_in_graders_warn(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _stamp(old, tree="repo@abc", eval_dir="e1", image="img:1", graders="g1")
    _stamp(new, tree="repo@abc", eval_dir="e1", image="img:1", graders="g2")
    _cell(old, "fix_a", "guidance:raw", 1)
    _cell(new, "fix_a", "guidance:raw", 1)
    s = compare([old, new], "old/guidance:raw", ["new/guidance:raw"])
    assert s["warnings"] == ["new/guidance:raw vs old/guidance:raw: the batches ran from different graders (g1, g2)"]
