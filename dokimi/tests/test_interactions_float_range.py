# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Interaction contrasts of finite scores near the float range stay finite or refuse.

Follow-up of #599 (https://github.com/intel/dokimi/issues/599) for eval/interactions.py: #638.
"""

from __future__ import annotations

import math

import pytest

from dokimi.eval.interactions import interaction_significance, interaction_test

BIG = 1.7e308
ARMS = ["baseline", "a", "b", "a+b"]


def _ev(qid: str, ab: float, a: float, b: float, base: float) -> dict:
    scores = {"a+b": ab, "a": a, "b": b, "baseline": base}
    return {"question_id": qid, "scores": {k: {"aggregate": v} for k, v in scores.items()}}


def _test(evaluations):
    return interaction_test(evaluations, arm_a="a", arm_b="b", combined_arm="a+b", baseline_arm="baseline")


def test_a_contrast_beyond_the_float_range_raises_instead_of_an_inf_delta():
    evaluations = [_ev("q1", BIG, -BIG, 0.0, 0.0), _ev("q2", 0.0, 0.0, 0.0, 0.0)]
    with pytest.raises(OverflowError, match="leaves the float range"):
        _test(evaluations)
    with pytest.raises(OverflowError, match="leaves the float range"):
        interaction_significance(evaluations, ARMS, "baseline")


@pytest.mark.parametrize(
    ("ab", "a", "b", "base"),
    [
        (BIG, 0.0, 0.0, -BIG),  # the contrast is 0; only ab - base leaves the range
        (0.0, BIG, -BIG, -BIG),  # the contrast is -BIG; only a - base leaves the range
        (0.0, -BIG, BIG, -BIG),  # the contrast is -BIG; only b - base leaves the range
    ],
)
def test_an_arm_delta_beyond_the_float_range_raises_while_the_contrast_is_finite(ab, a, b, base):
    evaluations = [_ev("q1", ab, a, b, base), _ev("q2", 0.0, 0.0, 0.0, 0.0)]
    with pytest.raises(OverflowError, match="a difference of the paired values leaves the float range"):
        _test(evaluations)


@pytest.mark.filterwarnings("ignore:invalid value encountered in matmul")  # the interval search of a delta this large
def test_a_contrast_that_fits_is_returned_when_a_step_of_the_sum_overflows():
    # ab - a is inf, but ab - a - b + base = BIG and every arm delta is finite
    evaluations = [_ev("q1", BIG, -BIG, BIG, 0.0), _ev("q2", 0.0, 0.0, 0.0, 0.0)]
    res = _test(evaluations)
    assert res is not None
    assert res["interaction"] == BIG / 2
    assert math.isfinite(res["delta_std"])


def test_a_near_range_contrast_that_stays_finite_returns_finite_numbers():
    evaluations = [_ev("q1", BIG, BIG, BIG, BIG), _ev("q2", -BIG, -BIG, -BIG, -BIG), _ev("q3", 1.0, 0.0, 0.0, 0.0)]
    res = _test(evaluations)
    assert res is not None
    assert res["n"] == 3
    for key in ("interaction", "delta_a", "delta_b", "delta_combined", "additive_expectation"):
        assert math.isfinite(res[key]), key
    assert res["delta_a"] == 0.0
    assert res["delta_combined"] == 0.33
