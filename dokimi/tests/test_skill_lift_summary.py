# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill-lift reporting on the task track: Wilson CI, normalized gain,
skill-invocation rate and cost per pass."""

from __future__ import annotations

import pytest

from dokimi.artifacts import ArtifactValidationError, validate_artifact
from dokimi.eval.binary_stats import normalized_change, normalized_gain, wilson_interval
from dokimi.harnesses.runner import summarize_task_results
from dokimi.report.task_runs_report import render_task_runs_report


@pytest.mark.parametrize(
    "k, n, expected",
    [
        # Reference values (Wilson 1927; e.g. statsmodels proportion_confint method="wilson").
        (5, 10, (0.2366, 0.7634)),
        (0, 10, (0.0, 0.2775)),
        (10, 10, (0.7225, 1.0)),
        (1, 1, (0.2065, 1.0)),
    ],
)
def test_wilson_interval_matches_reference(k, n, expected):
    assert wilson_interval(k, n) == expected


def test_wilson_interval_empty_and_invalid():
    assert wilson_interval(0, 0) == (None, None)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_wilson_interval_honours_confidence():
    # Wilson's closed form at z = 1.6449 (90%) and z = 2.5758 (99%).
    assert wilson_interval(5, 10, 0.90) == (0.2693, 0.7307)
    assert wilson_interval(5, 10, 0.99) == (0.1842, 0.8158)


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.5, 1.5])
def test_wilson_interval_rejects_confidence_outside_unit_interval(confidence):
    # 1.0 used to raise StatisticsError, 0 gave a zero-width and < 0 an inverted interval.
    with pytest.raises(ValueError, match="confidence"):
        wilson_interval(5, 10, confidence)


@pytest.mark.parametrize(
    "arm, base, expected",
    [(0.505, 0.339, 0.2511), (0.3, 0.3, 0.0), (0.2, 0.4, -0.3333),
     # Hake's form is unbounded below
     (0.5, 0.95, -9.0), (1.0, 1.0, None), (None, 0.5, None), (0.5, None, None)],
)
def test_normalized_gain_is_hakes_form(arm, base, expected):
    assert normalized_gain(arm, base) == expected


@pytest.mark.parametrize(
    "arm, base, expected",
    [(0.505, 0.339, 0.2511),           # a gain: same as Hake
     (0.2, 0.4, -0.5), (0.5, 0.95, -0.4737), (0.0, 0.9, -1.0),   # a loss: share of passes lost
     (0.3, 0.3, 0.0),                                            # equal: 0
     # both at the ceiling or the floor: Marx & Cummings drop the case
     (1.0, 1.0, None), (0.0, 0.0, None),
     (1.0, 0.0, 1.0), (0.0, 1.0, -1.0), (None, 0.5, None)],
)
def test_normalized_change_is_bounded(arm, base, expected):
    assert normalized_change(arm, base) == expected


def test_normalized_change_stays_in_bounds():
    rates = [i / 20 for i in range(21)]
    values = [normalized_change(a, b) for a in rates for b in rates]
    assert all(v is None or -1.0 <= v <= 1.0 for v in values)


def _row(task, harness, passed, *, cost=None, inv=None, error=None, repeat=1):
    metrics = {"operation_count": 0, "operations_by_type": {}}
    if cost is not None:
        metrics["cost_usd"] = cost
    if inv is not None:
        metrics["n_skill_invocations"] = inv
    if error:
        metrics["error"] = error
    return {
        "task": task, "harness": harness, "model": "m", "command": [], "returncode": 0,
        "elapsed_sec": 1.0, "reward": (1.0 if passed else 0.0) if not error else None,
        "passed": passed, "stdout_tail": "", "stderr_tail": "", "output_dir": None,
        "operations": [], "metrics": metrics, "repeat": repeat,
    }


def _rows():
    base, skill = "benchflow:a", "benchflow-skill:a"
    return [
        _row("t1", base, False, cost=0.1, inv=0),
        _row("t2", base, True, cost=0.1, inv=0),
        _row("t3", base, False, cost=0.1, inv=0),
        _row("t4", base, False, cost=0.1, inv=0),
        _row("t1", skill, True, cost=0.2, inv=2),
        _row("t2", skill, True, cost=0.2, inv=1),
        _row("t3", skill, False, cost=0.2, inv=0),
        # Reports invocations but errored: excluded because it errored, not
        # because it is silent.
        _row("t4", skill, False, inv=5, error="benchflow: install_failure"),
    ]


def test_summary_reports_ci_gain_invocation_and_cost_per_pass():
    summary = summarize_task_results(_rows(), baseline_harness="benchflow:a")
    base = summary["per_harness"]["benchflow:a"]
    skill = summary["per_harness"]["benchflow-skill:a"]
    assert base["pass_rate_ci"] == {"lo": 0.0456, "hi": 0.6994, "confidence": 0.95, "method": "wilson"}
    assert base["skill_invocation_rate"] == 0.0
    # The errored row is not a skill-use observation.
    assert skill["skill_invocation_rate"] == round(2 / 3, 4)
    assert skill["skill_invocation_n"] == 3
    assert base["cost"]["cost_per_pass_usd"] == pytest.approx(0.4)
    # One row unpriced -> no per-pass cost rather than an understated one.
    assert skill["cost"]["cost_per_pass_usd"] is None
    # Same denominator as pass_rate: the errored row is not a trial.
    assert skill["pass_rate"] == round(2 / 3, 4)
    assert skill["pass_rate_ci"]["lo"] == wilson_interval(2, 3)[0]
    # Withheld like pass_rate_delta once an arm lost a cell.
    assert summary["comparisons"]["benchflow-skill:a"]["normalized_gain"] is None
    assert summary["comparisons"]["benchflow-skill:a"]["normalized_change"] is None


def test_normalized_gain_when_both_arms_scored_every_cell():
    rows = [r for r in _rows() if r["task"] != "t4"]
    summary = summarize_task_results(rows, baseline_harness="benchflow:a")
    # 2/3 vs 1/3 -> half of the baseline's headroom.
    assert summary["comparisons"]["benchflow-skill:a"]["normalized_gain"] == 0.5
    assert summary["comparisons"]["benchflow-skill:a"]["normalized_change"] == 0.5


def test_harness_without_skill_telemetry_reports_null_rate():
    rows = [_row("t1", "docker-oracle", True)]
    stats = summarize_task_results(rows)["per_harness"]["docker-oracle"]
    assert stats["skill_invocation_rate"] is None
    assert stats["skill_invocation_n"] == 0


def test_artifact_with_new_fields_validates_and_renders():
    rows = _rows()
    gain_md = render_task_runs_report({
        "summary": summarize_task_results(
            [r for r in rows if r["task"] != "t4"], baseline_harness="benchflow:a"),
        "results": [], "tasks": [], "baseline_harness": "benchflow:a",
    })
    assert "+0.50" in gain_md
    data = {
        "schema_version": "task_runs.v1",
        "generated_at": "2026-10-03T00:00:00Z",
        "model": "m",
        "baseline_harness": "benchflow:a",
        "harnesses": ["benchflow:a", "benchflow-skill:a"],
        "repeats": 1,
        "tasks": [],
        "results": rows,
        "summary": summarize_task_results(rows, baseline_harness="benchflow:a"),
    }
    validate_artifact("task_runs", data)
    md = render_task_runs_report(data)
    assert "95% CI (Wilson)" in md
    assert "5–70%" in md
    assert "67% (3)" in md
    # A 0% rate is a measurement, not a missing one.
    assert "| 0% (4) |" in md
    # Withheld with pass_rate_delta, and the cell says so.
    assert "| — (withheld) | — (withheld) |" in md


def test_gain_and_change_diverge_on_a_loss():
    """3/4 -> 1/4: Hake's gain is -2.0, the normalized change -0.6667 (summary and report)."""
    base, skill = "docker-claude", "docker-claude-skill"
    rows = [_row(f"t{i}", base, i < 3, cost=0.1) for i in range(4)]
    rows += [_row(f"t{i}", skill, i == 0, cost=0.1) for i in range(4)]
    summary = summarize_task_results(rows, baseline_harness=base)
    cmp = summary["comparisons"][skill]
    assert (cmp["normalized_gain"], cmp["normalized_change"]) == (-2.0, -0.6667)
    md = render_task_runs_report({"summary": summary, "results": [], "tasks": [],
                                  "baseline_harness": base})
    assert "| -2.00 | -0.67 |" in md
    # $/pass is total / passes, not the total: $0.40 over 3 passes.
    assert "| $0.4000 | $0.1333 |" in md


def test_change_at_shared_ceiling_is_null_and_says_why():
    base, skill = "a", "b"
    rows = [_row(f"t{i}", h, True) for i in range(2) for h in (base, skill)]
    summary = summarize_task_results(rows, baseline_harness=base)
    cmp = summary["comparisons"][skill]
    assert cmp["normalized_gain"] is None and cmp["normalized_change"] is None
    md = render_task_runs_report({"summary": summary, "results": [], "tasks": [],
                                  "baseline_harness": base})
    assert "| +0.00 pp | — (baseline at 100%) | — (both at 0% or 100%) |" in md


def test_artifact_without_normalized_keys_gives_no_reason():
    """An artifact written before the normalized_* keys shows a plain dash, not
    "baseline at 100%" for a baseline that was at 50%."""
    base, skill = "a", "b"
    rows = [_row("t0", base, True), _row("t1", base, False),
            _row("t0", skill, True), _row("t1", skill, True)]
    summary = summarize_task_results(rows, baseline_harness=base)
    for key in ("normalized_gain", "normalized_change"):
        del summary["comparisons"][skill][key]
    md = render_task_runs_report({"summary": summary, "results": [], "tasks": [],
                                  "baseline_harness": base})
    assert "| `b` | +50.00 pp | — | — |" in md


def test_tiny_per_pass_cost_is_not_rendered_as_zero():
    rows = [_row("t1", "a", True, cost=0.00001)]
    md = render_task_runs_report({"summary": summarize_task_results(rows), "results": [],
                                  "tasks": [], "baseline_harness": None})
    assert "<$0.0001" in md and "$0.0000" not in md


@pytest.mark.parametrize("inv, reported", [(1.0, 1), (-1, 0), (1.5, 0), ("2", 0), (True, 0)])
def test_invocation_count_must_be_a_non_negative_whole_number(inv, reported):
    rows = [_row("t1", "a", True, inv=inv)]
    assert summarize_task_results(rows)["per_harness"]["a"]["skill_invocation_n"] == reported


def test_bool_cost_is_not_a_price():
    rows = [_row("t1", "a", True, cost=True)]
    cost = summarize_task_results(rows)["per_harness"]["a"]["cost"]
    assert cost["cost_known_n"] == 0 and cost["cost_per_pass_usd"] is None


def test_bool_reward_is_not_a_score():
    """True is an int, so it used to enter avg_reward as 1.0 — a perfect score."""
    rows = [_row("t1", "a", False), _row("t2", "a", False)]
    rows[0]["reward"] = True
    rows[1]["reward"] = False
    stats = summarize_task_results(rows)["per_harness"]["a"]
    assert stats["avg_reward"] is None


@pytest.mark.parametrize("path, value", [
    (("summary", "comparisons", "benchflow-skill:a", "normalized_change"), -1.5),
    (("summary", "comparisons", "benchflow-skill:a", "normalized_gain"), 1.5),
    (("summary", "per_harness", "benchflow:a", "skill_invocation_rate"), 1.2),
    (("summary", "per_harness", "benchflow:a", "pass_rate_ci", "hi"), 1.1),
    (("summary", "per_harness", "benchflow:a", "pass_rate_ci", "confidence"), 1.0),
    (("summary", "per_harness", "benchflow:a", "pass_rate_ci", "method"), "wald"),
    (("results", 0, "metrics", "n_skill_invocations"), -1),
])
def test_schema_bounds_reject_out_of_range_values(path, value):
    rows = [r for r in _rows() if r["task"] != "t4"]
    data = {
        "schema_version": "task_runs.v1", "generated_at": "2026-10-03T00:00:00Z",
        "model": "m", "baseline_harness": "benchflow:a",
        "harnesses": ["benchflow:a", "benchflow-skill:a"], "repeats": 1, "tasks": [],
        "results": rows, "summary": summarize_task_results(rows, baseline_harness="benchflow:a"),
    }
    validate_artifact("task_runs", data)
    node = data
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ArtifactValidationError):
        validate_artifact("task_runs", data)


def test_bool_invocation_flag_is_not_counted():
    rows = [_row("t1", "benchflow-skill:a", True, inv=True)]
    stats = summarize_task_results(rows)["per_harness"]["benchflow-skill:a"]
    assert stats["skill_invocation_n"] == 0
    assert stats["skill_invocation_rate"] is None


def test_errored_row_marked_passed_does_not_crash_or_count():
    """A hand-merged row with passed=True and metrics.error is not a pass."""
    rows = [_row("t1", "a", True), {**_row("t2", "a", True), "metrics": {"error": "x"}}]
    stats = summarize_task_results(rows)["per_harness"]["a"]
    assert stats["passed"] == 1 and stats["scored"] == 1
    assert stats["per_task"]["t2"]["passed"] == 0
    assert stats["pass_rate_ci"]["hi"] == 1.0


def test_gain_fields_survive_a_cell_both_arms_lost():
    """Same errored cell on both sides cancels out (#79's rule), so the gain is computed."""
    base, skill = "benchflow:a", "benchflow-skill:a"
    rows = [
        _row("t1", base, False), _row("t2", base, True), _row("t3", base, False, error="build"),
        _row("t1", skill, True), _row("t2", skill, True), _row("t3", skill, False, error="build"),
    ]
    cmp = summarize_task_results(rows, baseline_harness=base)["comparisons"][skill]
    assert cmp["normalized_gain"] == 1.0
    assert cmp["normalized_change"] == 1.0
