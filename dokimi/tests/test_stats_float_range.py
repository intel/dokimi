# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Finite inputs near the float range keep finite results in eval/stats.py.

Follow-ups of #572: https://github.com/intel/dokimi/issues/598 to #601.
"""

from __future__ import annotations

import math

import pytest

from dokimi.eval import stats

BIG = 1.7e308


def test_bootstrap_ci_of_values_whose_sum_overflows_reports_their_mean():
    ci = stats.bootstrap_ci([BIG] * 3)
    assert (ci["mean"], ci["lo"], ci["hi"]) == (BIG, BIG, BIG)


def test_bootstrap_ci_of_mixed_signs_near_the_maximum_stays_inside_the_data_range():
    ci = stats.bootstrap_ci([BIG, -BIG, BIG], n_resamples=200)
    assert ci["mean"] == pytest.approx(BIG / 3, rel=1e-12)
    assert all(math.isfinite(ci[k]) for k in ("lo", "hi"))
    assert -BIG <= ci["lo"] <= ci["hi"] <= BIG


@pytest.mark.parametrize("scipy", [False, True])
def test_mean_ci_of_values_whose_sum_overflows_is_finite(monkeypatch, scipy):
    monkeypatch.setattr(stats, "HAS_SCIPY", scipy)
    ci = stats.mean_ci([BIG] * 3)
    assert (ci["mean"], ci["lo"], ci["hi"]) == (BIG, BIG, BIG)


def test_mean_ci_of_one_huge_value_is_that_value(monkeypatch):
    monkeypatch.setattr(stats, "HAS_SCIPY", True)
    ci = stats.mean_ci([BIG])
    assert (ci["mean"], ci["lo"], ci["hi"], ci["n"]) == (BIG, BIG, BIG, 1)


def test_bootstrap_ci_keeps_a_real_inf_in_the_mean():
    ci = stats.bootstrap_ci([math.inf, 1.0, 2.0], n_resamples=50)
    assert ci["mean"] == math.inf


def _evals(with_score, without_score, n=3):
    return [{"id": f"q{i}", "with_docs": {"aggregate": with_score}, "without_docs": {"aggregate": without_score}}
            for i in range(n)]


def test_paired_test_refuses_deltas_beyond_the_float_range():
    with pytest.raises(OverflowError, match="deltas too large"):
        stats.paired_test([BIG] * 4, [-BIG] * 4)


def test_paired_test_of_a_delta_just_inside_the_range_still_runs():
    result = stats.paired_test([BIG] * 4, [BIG - 1e300] * 4)
    assert result["delta_mean"] == pytest.approx(1e300, rel=1e-9)
    assert stats._deltas([BIG], [-9e306]) == [BIG + 9e306]


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_paired_test_keeps_the_meaning_of_a_non_finite_input(bad):
    result = stats.paired_test([bad, 1.0, 2.0], [0.0, 0.0, 0.0])
    assert result["p"] is None
    assert result["significant"] is None


def test_paired_summary_paired_delta_and_arm_significance_refuse_the_same_deltas():
    evals = _evals(BIG, -BIG)
    with pytest.raises(OverflowError):
        stats.paired_summary(evals)
    with pytest.raises(OverflowError):
        stats.paired_delta(evals, "without_docs", "with_docs")
    with pytest.raises(OverflowError):
        stats.arm_significance(evals, "without_docs")


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_point_biserial_refuses_a_total_that_is_not_finite(bad):
    result = stats.point_biserial([1, 1, 0, 0], [bad, 1.0, 2.0, 3.0])
    assert result == {"r": None, "n": 4, "n_pass": 2, "reason": "a total is not finite"}


@pytest.mark.parametrize("flags, totals, reason", [
    ([1, 0], [math.inf, 1.0], "fewer than 3 respondents"),
    ([1, 1, 1], [math.inf, 1.0, 2.0], "no variance in the item: every respondent scored it the same"),
    ([0, 0, 0, 0], [math.nan, 1.0, 2.0, 3.0], "no variance in the item: every respondent scored it the same"),
])
def test_point_biserial_keeps_the_older_refusals_ahead_of_the_non_finite_one(flags, totals, reason):
    assert stats.point_biserial(flags, totals)["reason"] == reason


def test_paired_summary_of_ordinary_scores_is_unchanged():
    s = stats.paired_summary(_evals(80.0, 60.0))
    assert (s.delta_mean, s.delta_min, s.delta_max, s.improvements) == (20.0, 20.0, 20.0, 3)
