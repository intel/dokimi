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
