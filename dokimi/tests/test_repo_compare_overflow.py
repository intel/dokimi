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


@pytest.mark.parametrize("deltas", [[1.7e308] * 4, [1.7e308, -1.7e308, 1.7e308], [1.7e308, 1.7e308, -1.7e308]])
def test_sign_flip_summary_refuses_finite_deltas_whose_sum_or_spread_leaves_the_range(deltas):
    with pytest.raises(CompareError, match="float range"):
        sign_flip_summary([_task(d) for d in deltas])


def test_sign_flip_summary_refuses_deltas_the_test_cannot_shift_within_the_range():
    with pytest.raises(CompareError, match="too large to test"):
        sign_flip_summary([_task(1.0e307)] * 6 + [_task(-1.0e307)] * 6)


def test_checked_mean_is_the_plain_mean_where_it_is_finite():
    assert checked_mean([0.1, 0.2, 0.4]) == sum([0.1, 0.2, 0.4]) / 3
    assert checked_mean([1.0e307, -1.0e307, 1.0e307]) == sum([1.0e307, -1.0e307, 1.0e307]) / 3
    assert checked_mean([math.inf, 1.0]) == math.inf
    assert math.isnan(checked_mean([math.nan, 1.0]))


def test_checked_delta_refuses_an_integer_beyond_the_float_range_with_the_requested_error():
    with pytest.raises(CompareError, match="float range"):
        checked_delta(10**309, 0.0, CompareError)
    with pytest.raises(CompareError, match="float range"):
        checked_delta(0.0, -(10**309), CompareError)


def test_checked_mean_and_sign_flip_summary_refuse_integers_whose_sum_leaves_the_float_range():
    with pytest.raises(CompareError, match="float range"):
        checked_mean([10**308, 10**308], CompareError)
    with pytest.raises(CompareError, match="float range"):
        sign_flip_summary([_task(10**308), _task(10**308)])


def test_checked_mean_refuses_finite_values_whose_magnitudes_sum_beyond_the_range():
    with pytest.raises(CompareError, match="float range"):
        checked_mean([1.7e308, -1.7e308, 1.7e308], CompareError)
