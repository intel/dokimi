# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An integer beyond the float range is not a finite number: it is unscored, not a crash.

``float(10**400)`` raises OverflowError, so a reward that large used to abort
``summarize_task_results`` and the ``tasks`` cell state instead of counting the row as unscored.
"""

from __future__ import annotations

import math

import pytest

from dokimi.measure import as_float


@pytest.mark.parametrize("value, expected", [
    (0, 0.0),
    (3, 3.0),
    (-2.5, -2.5),
    (10**308, 1e308),
    (10**400, None),
    (-(10**400), None),
    (float("nan"), None),
    (float("inf"), None),
    (True, None),
    (False, None),
    ("1.0", None),
    (None, None),
])
def test_as_float_returns_a_finite_float_or_none(value, expected):
    result = as_float(value)
    assert result == expected
    assert result is None or isinstance(result, float)


def test_as_float_of_a_small_int_is_a_float():
    result = as_float(7)
    assert isinstance(result, float) and math.isfinite(result)


def test_summarize_task_results_counts_a_reward_beyond_the_float_range_as_unscored():
    from dokimi.harnesses.runner import summarize_task_results

    rows = [
        {"task": "t", "harness": "h", "passed": True, "elapsed_sec": 1, "reward": 10**400, "metrics": {}},
        {"task": "t2", "harness": "h", "passed": True, "elapsed_sec": 1, "reward": 1.0, "metrics": {}},
    ]
    summary = summarize_task_results(rows)
    assert summary["per_harness"]["h"]["avg_reward"] == 1.0


@pytest.mark.parametrize("rewards, expected", [
    ([1e308, 1e308], 1e308),
    ([1e308, 1e308, -1e308, -1e308], 0.0),
])
def test_summarize_task_results_keeps_a_finite_mean_when_the_sum_overflows(rewards, expected):
    from dokimi.harnesses.runner import summarize_task_results

    rows = [{"task": "t", "harness": "h", "reward": reward} for reward in rewards]
    assert summarize_task_results(rows)["per_harness"]["h"]["avg_reward"] == expected


@pytest.mark.parametrize("elapsed, expected", [
    ("12.5", 12.5),
    ("nan", 0.0),
    ("inf", 0.0),
    ("unavailable", 0.0),
])
def test_summarize_task_results_reads_finite_elapsed_strings(elapsed, expected):
    from dokimi.harnesses.runner import summarize_task_results

    rows = [{"task": "t", "harness": "h", "elapsed_sec": elapsed}]
    assert summarize_task_results(rows)["per_harness"]["h"]["elapsed_sec"] == expected


def test_tasks_completion_counts_a_reward_beyond_the_float_range_as_unscored(capsys):
    from dokimi.commands.tasks import _check_completion

    row = {"task": "t", "harness": "h", "reward": 10**400}
    with pytest.raises(SystemExit) as exc:
        _check_completion({"tasks": [{"name": "t"}], "harnesses": ["h"], "results": [row]})
    assert exc.value.code == 1
    assert "1 unscored" in capsys.readouterr().err


def test_task_runs_report_shows_a_reward_beyond_the_float_range_as_null():
    from dokimi.harnesses.runner import summarize_task_results
    from dokimi.report.task_runs_report import render_task_runs_report

    rows = [{"task": "t", "harness": "h", "passed": False, "reward": 10**400, "returncode": 0,
             "output_dir": None, "metrics": {}}]
    md = render_task_runs_report({
        "harnesses": ["h"], "tasks": [{"name": "t", "metadata": {}}], "results": rows,
        "summary": summarize_task_results(rows),
    })
    assert "| `h` | 1 | null |" in md


@pytest.mark.parametrize("field", ["elapsed_sec", "operation_count", "n_skill_invocations"])
def test_summarize_task_results_survives_other_fields_beyond_the_float_range(field):
    from dokimi.harnesses.runner import summarize_task_results

    metrics: dict[str, object] = {}
    row: dict[str, object] = {"task": "t", "harness": "h", "passed": True, "elapsed_sec": 1,
                              "reward": 1.0, "metrics": metrics}
    if field == "elapsed_sec":
        row[field] = 10**400
    else:
        metrics[field] = 10**400
    stats = summarize_task_results([row])["per_harness"]["h"]
    assert stats["avg_reward"] == 1.0
    if field == "operation_count":
        assert stats["operation_count"] == 10**400


def test_a_zero_skill_invocation_count_is_a_reported_row():
    from dokimi.harnesses.runner import summarize_task_results

    row = {"task": "t", "harness": "h", "passed": True, "elapsed_sec": 1, "reward": 1.0,
           "metrics": {"n_skill_invocations": 0}}
    assert summarize_task_results([row])["per_harness"]["h"]["skill_invocation_n"] == 1
