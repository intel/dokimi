# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Deltas and means of ``repo compare`` that leave the float range are refused, not carried as inf (#639)."""

from __future__ import annotations

import math

import pytest

from dokimi.eval.finite_delta import checked_delta, checked_mean
from dokimi.repos.compare import CompareError, _pair, sign_flip_summary


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


@pytest.mark.filterwarnings("ignore:overflow encountered:RuntimeWarning")  # the mde of a spread this large
def test_sign_flip_summary_shifts_deltas_near_the_range_on_a_scaled_copy():
    row = sign_flip_summary([_task(1.0e307), _task(-1.0e307)] * 6)  # cancels pairwise: sum() before 3.12 does not compensate
    assert row["delta"]["mean"] == 0.0
    assert math.isfinite(row["delta"]["lo"]) and math.isfinite(row["delta"]["hi"])
    assert row["delta"]["lo"] < 0 < row["delta"]["hi"]


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
