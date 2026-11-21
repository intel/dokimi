# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Deltas and means of ``repo compare`` that leave the float range are refused, not carried as inf (#639)."""

from __future__ import annotations

import math
import sys

import numpy as np
import pytest

from dokimi.eval.finite_delta import checked_delta, checked_mean
from dokimi.repos.compare import CompareError, _compare_measure, _inconclusive_note, _pair, _round, sign_flip_summary
from dokimi.repos.compare_values import task_values
from dokimi.repos.compare_reach import min_detectable
from dokimi.repos.grading import SCORE_BOUND
from dokimi.repos.useful import useful_counts, useful_task_counts, useful_task_values


def _side(value: float) -> dict:
    return {"values": {"m": value}, "n": {"m": 1}}


def _task(delta: float) -> dict:
    return {"task": "t", "baseline": 0.0, "treatment": delta, "delta": delta}


def test_pair_refuses_a_difference_beyond_the_float_range():
    with pytest.raises(CompareError, match="float range"):
        _pair("t", _side(-1.7e308), _side(1.7e308), "m")


def test_pair_keeps_a_difference_near_the_range():
    out = _pair("t", _side(1.0e308), _side(1.7e308), "m")
    assert out is not None
    assert out["delta"] == 1.7e308 - 1.0e308


def test_pair_keeps_an_inf_value_meaning():
    out = _pair("t", _side(0.0), _side(math.inf), "m")
    assert out is not None
    assert out["delta"] == math.inf


def test_pair_keeps_an_inf_baseline_meaning():
    out = _pair("t", _side(math.inf), _side(0.0), "m")
    assert out is not None
    assert out["delta"] == -math.inf


def test_pair_names_the_task_of_a_difference_beyond_the_range():
    with pytest.raises(CompareError, match=r"^task 't': the difference of"):
        _pair("t", _side(-1.7e308), _side(1.7e308), "m")


def test_an_inf_beside_an_integer_beyond_the_float_range_keeps_its_meaning():
    assert checked_delta(math.inf, 10**309, CompareError) == math.inf
    assert checked_delta(10**309, math.inf, CompareError) == -math.inf
    assert checked_mean([math.inf, 10**309], CompareError) == math.inf


def test_checked_mean_keeps_a_negative_inf_when_finite_values_would_sum_past_the_range():
    assert checked_mean([10**309, 1e308, 1e308, -math.inf], CompareError) == -math.inf


@pytest.mark.parametrize("values, mean", [([1e308, 1e308, -math.inf], -math.inf), ([-1e308, -1e308, math.inf], math.inf),
                                          ([-math.inf, 1e308, 1e308], -math.inf)])
def test_checked_mean_keeps_an_inf_whatever_the_order_of_finite_floats_beside_it(values, mean):
    assert checked_mean(values, CompareError) == mean


@pytest.mark.parametrize("deltas", [[1.7e308] * 4, [1.7e308, -1.7e308, 1.7e308], [1.7e308, 1.7e308, -1.7e308]])
def test_sign_flip_summary_refuses_finite_deltas_whose_sum_or_spread_leaves_the_range(deltas):
    with pytest.raises(CompareError, match="float range"):
        sign_flip_summary([_task(d) for d in deltas])


def test_sign_flip_summary_shifts_deltas_near_the_range_on_a_scaled_copy():
    row = sign_flip_summary([_task(1.0e307), _task(-1.0e307)] * 6)  # cancels pairwise: sum() before 3.12 does not compensate
    assert row["delta"]["mean"] == 0.0
    assert math.isfinite(row["delta"]["lo"]) and math.isfinite(row["delta"]["hi"])
    assert row["delta"]["lo"] < 0 < row["delta"]["hi"]


@pytest.mark.filterwarnings("error")
def test_mde_is_none_when_the_planning_figure_leaves_the_float_range():
    row = sign_flip_summary([_task(d) for d in [sys.float_info.max, 1.0, 1.0, 1.0, 1.0, 1.0]])
    assert row["mde"] is None


@pytest.mark.filterwarnings("error")
def test_mde_of_a_large_spread_is_rounded_without_overflowing():
    row = sign_flip_summary([_task(1.0e307), _task(-1.0e307)] * 6)
    assert math.isfinite(row["mde"]) and row["mde"] == pytest.approx(9.276e306, rel=1e-3)


@pytest.mark.filterwarnings("error")
def test_min_detectable_past_the_range_is_inf_without_a_warning():
    assert min_detectable(sys.float_info.max, 3) == math.inf


def test_min_detectable_of_no_task_is_nan():
    assert math.isnan(min_detectable(0.5, 0))


@pytest.mark.filterwarnings("error")
def test_round_of_a_numpy_scalar_near_the_range_does_not_warn():
    assert _round(np.float64(1.0e307)) == 1.0e307


def test_inconclusive_note_prints_a_large_mde_in_scientific_notation():
    row = sign_flip_summary([_task(1.0e307), _task(-1.0e307)] * 6)
    note = _inconclusive_note({**row, "measure": "gate"}, 1)
    assert "about ±9.276e+306 (12 tasks)" in note


def test_inconclusive_note_keeps_three_decimals_for_a_small_mde():
    row = {"measure": "gate", "n_tasks": 6, "delta": {"lo": -1.0, "hi": 1.0}, "mde": 123456.7891}
    assert "about ±123456.789 (6 tasks)" in _inconclusive_note(row, 1)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_round_gives_no_figure_for_a_non_finite_value(value):
    assert _round(value) is None


def test_checked_mean_is_the_plain_mean_where_it_is_finite():
    assert checked_mean([0.1, 0.2, 0.4]) == math.fsum([0.1, 0.2, 0.4]) / 3  # fsum: sum() before 3.12 can differ in the last bit
    assert checked_mean([1.0e307, -1.0e307, 1.0e307]) == math.fsum([1.0e307, -1.0e307, 1.0e307]) / 3
    assert checked_mean([math.inf, 1.0]) == math.inf
    assert math.isnan(checked_mean([math.nan, 1.0]))


def test_checked_mean_sums_exactly_whatever_the_python_version():
    assert checked_mean([1e307] * 6 + [-1e307] * 6) == 0.0
    assert checked_mean([1, 1e16, 1.0, -1e16]) == 0.5  # sum() starts on an int and loses its compensation: 0.25


def test_checked_mean_refuses_a_total_that_only_the_exact_sum_sees_beyond_the_range(monkeypatch):
    def left_to_right(values, start=0):  # what sum() does before Python 3.12
        total = start
        for v in values:
            total += v
        return total

    monkeypatch.setattr("dokimi.eval.finite_delta.sum", left_to_right, raising=False)
    values = [float.fromhex("0x1.fffffffffffffp+1023"), 2.0**969, 2.0**969]
    assert math.isfinite(left_to_right(values))  # the rounded total stays finite
    with pytest.raises(CompareError, match=r"task 't': the sum of the magnitudes .* float range"):
        checked_mean(values, CompareError, "task 't'")


def test_checked_mean_keeps_the_exact_mean_of_integers():
    assert checked_mean([2**60 + 1, 1, -(2**60)]) == 2 / 3  # fsum would round the first to a float: 1 / 3
    assert checked_mean([2**60 + 1, 2**60 + 1]) == float(2**60 + 1)


def test_checked_mean_keeps_integer_contributions_beside_floats():
    assert checked_mean([2**53 + 1, -(2**53), 0.0]) == 1 / 3  # fsum rounds the first to 2**53: 0.0
    assert checked_mean([2**60 + 1, -(2**60), 0.5]) == 0.5


def test_checked_mean_refuses_a_mixed_total_that_only_the_exact_sum_sees_beyond_the_range():
    values = [float.fromhex("0x1.fffffffffffffp+1023"), 2**969, 2.0**969]  # a left-to-right sum rounds back to the maximum
    with pytest.raises(CompareError, match=r"task 't': the sum of the magnitudes .* float range"):
        checked_mean(values, CompareError, "task 't'")


@pytest.mark.parametrize("order", ["float first", "float between"])
def test_checked_mean_takes_integers_whose_exact_total_fits_the_float_range(order):
    top = int(float.fromhex("0x1.fffffffffffffp+1023"))
    low, high = top - 2**970 + 1, 2**970 - 1
    values = [0.0, low, high] if order == "float first" else [low, 0.0, high]  # a float among the integers: sum() rounded the partial total and overflowed
    assert checked_mean(values, CompareError) == top / 3


def test_sign_flip_summary_mean_is_the_exact_mean():
    row = sign_flip_summary([_task(d) for d in (1, 1e16, 1.0, -1e16)])
    assert row["delta"]["mean"] == 0.5


def test_sign_flip_summary_leaves_values_past_the_measure_bound_out_of_the_means():
    # repo-evaluation-contract.md, "The measure bound": a value past SCORE_BOUND is no measurement, so its mean is None, not an overflow (#866)
    def rows(value: float) -> list[dict]:
        return [{"task": f"t{i}", "baseline": value, "treatment": value, "delta": 0.0} for i in range(3)]

    for value in (1.7e308, 2 * SCORE_BOUND):
        past = sign_flip_summary(rows(value), "metric:x")
        assert (past["baseline_mean"], past["treatment_mean"]) == (None, None)
    mixed = rows(1.0) + rows(2 * SCORE_BOUND)
    kept = sign_flip_summary(mixed, "metric:x")
    assert (kept["baseline_mean"], kept["treatment_mean"]) == (1.0, 1.0)
    at = sign_flip_summary(rows(SCORE_BOUND), "metric:x")
    assert (at["baseline_mean"], at["treatment_mean"]) == (SCORE_BOUND, SCORE_BOUND)


def test_compare_gives_no_row_for_a_metric_past_the_measure_bound_and_lists_its_tasks_unpaired():
    def arm(value: float) -> dict:
        cells = [{"model": "m", "task": f"t{i}", "batch": "b", "cell": f"c{i}", "score_task": False, "agent": {},
                  "metrics": {"x": value}, "grade": {"status": "ok", "success": True}} for i in range(3)]
        return task_values(cells, ("x",))

    shared = ["t0", "t1", "t2"]
    unpaired: list[dict] = []
    past = _compare_measure("tr", "m", "metric:x", shared, arm(1.7e308), arm(1.7e308), 0, unpaired)
    assert past is None
    assert [u["reason"] for u in unpaired] == ["no metric:x in baseline or treatment"] * 3
    at = _compare_measure("tr", "m", "metric:x", shared, arm(SCORE_BOUND), arm(SCORE_BOUND), 0, [])
    assert at is not None and at["baseline_mean"] == SCORE_BOUND


def _useful_cell(steps: float | None, reason: str | None = None) -> dict:
    return {"first_useful": {"kind": "edit", "steps": steps, "reason": reason, "issued_s": 1.0}}


@pytest.mark.parametrize("past", [1e200, float("nan"), float("inf")])
def test_first_useful_rate_leaves_out_a_cell_with_steps_past_the_bound(past):
    # contract "The measure bound": a cell whose steps is past it is neither a hit nor a miss (#922)
    cells = [_useful_cell(5), _useful_cell(past)]
    assert useful_task_values(cells)["first_useful_rate"] == [1.0]
    counts = useful_task_counts(cells)
    assert (counts["first_useful_steps"], counts["first_useful_rate"]) == (1, 1)


@pytest.mark.parametrize("reason", [None, "untimed"])
def test_first_useful_rate_leaves_out_a_cell_with_steps_none_whose_reason_says_it_hit(reason):
    # contract "The measure bound": a hit with no count is a broken count, not a miss; only `never` is a miss
    cells = [_useful_cell(5), _useful_cell(None, reason)]
    assert useful_task_values(cells)["first_useful_rate"] == [1.0]
    assert useful_task_counts(cells)["first_useful_rate"] == 1


def test_first_useful_rate_still_counts_a_never_cell_as_a_miss_beside_a_past_bound_cell():
    # contract "The measure bound": only a steps that is no measure is left out; a cell with steps None is a miss
    cells = [_useful_cell(5), _useful_cell(1e200), _useful_cell(None, "never")]
    assert useful_task_values(cells)["first_useful_rate"] == [0.5]
    assert useful_task_counts(cells)["first_useful_rate"] == 2


def test_first_useful_rate_keeps_a_cell_with_steps_at_the_bound_as_a_hit():
    # contract "The measure bound": a magnitude of 1e150 (SCORE_BOUND) is still a measurement
    cells = [_useful_cell(5), _useful_cell(SCORE_BOUND)]
    assert useful_task_values(cells)["first_useful_rate"] == [1.0]
    assert useful_task_counts(cells)["first_useful_rate"] == 2


def test_useful_counts_shows_a_cell_with_steps_past_the_bound_as_unmeasured():
    # contract "The measure bound": the compare header's observed-of-measured count leaves the cell out too (#922)
    cells = [_useful_cell(5), _useful_cell(1e200)]
    assert useful_counts(cells) == {"observed": 1, "measured": 1, "unmeasured": 1}


def test_checked_delta_refuses_an_integer_beyond_the_float_range_with_the_requested_error():
    with pytest.raises(CompareError, match="float range"):
        checked_delta(10**309, 0.0, CompareError)
    with pytest.raises(CompareError, match="float range"):
        checked_delta(0.0, -(10**309), CompareError)


def test_checked_delta_refuses_an_integer_past_the_digit_limit_with_the_requested_error():
    with pytest.raises(CompareError, match="float range"):
        checked_delta(10**5000, 0.0, CompareError)


def test_checked_mean_and_sign_flip_summary_refuse_integers_whose_sum_leaves_the_float_range():
    with pytest.raises(CompareError, match="float range"):
        checked_mean([10**308, 10**308], CompareError)
    with pytest.raises(CompareError, match="float range"):
        sign_flip_summary([_task(10**308), _task(10**308)])


def test_checked_mean_refuses_finite_values_whose_magnitudes_sum_beyond_the_range():
    with pytest.raises(CompareError, match="float range"):
        checked_mean([1.7e308, -1.7e308, 1.7e308], CompareError)
