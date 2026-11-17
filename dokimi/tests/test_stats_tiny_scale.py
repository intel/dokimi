# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The zero-variance check reads a sample at its own scale, not against an absolute floor of 1.0 (#640)."""

import pytest

from dokimi.eval import stats


@pytest.mark.parametrize("scale", [1e-300, 1e-12])
def test_values_that_vary_at_a_tiny_scale_get_a_student_t_interval(scale):
    pytest.importorskip("scipy")
    res = stats.mean_ci([1 * scale, 2 * scale, 3 * scale], ndigits=None)
    assert res["method"] == "student_t"
    assert res["lo"] < res["mean"] < res["hi"]


def test_mean_ci_is_scale_invariant_by_a_power_of_two():
    pytest.importorskip("scipy")
    base = stats.mean_ci([1.0, 2.0, 4.0], ndigits=None)
    small = stats.mean_ci([2.0**-40, 2.0**-39, 2.0**-38], ndigits=None)
    assert small["method"] == base["method"] == "student_t"
    assert small["lo"] / small["mean"] == base["lo"] / base["mean"]
    assert small["hi"] / small["mean"] == base["hi"] / base["mean"]


def test_a_constant_tiny_sample_stays_degenerate():
    pytest.importorskip("scipy")
    res = stats.mean_ci([1e-300] * 5, ndigits=None)
    assert (res["method"], res["lo"], res["hi"]) == ("degenerate", 1e-300, 1e-300)


def test_float_noise_around_a_tiny_constant_stays_degenerate():
    pytest.importorskip("scipy")
    noisy = [1e-12 * (1.0 + (1e-13 if i % 2 else -1e-13)) for i in range(6)]
    assert stats.mean_ci(noisy, ndigits=None)["method"] == "degenerate"
    assert stats.mean_ci([0.0] * 4)["method"] == "degenerate"


def test_cohens_d_of_tiny_deltas_is_a_number():
    assert stats._cohens_d(2e-300, 1e-300) == 2.0


def test_cohens_d_of_a_constant_tiny_shift_is_unbounded_not_a_number():
    assert stats._cohens_d(1e-300, 1e-320) is None
    assert stats._cohens_d(0.0, 0.0) == 0.0


def test_paired_test_of_tiny_deltas_reports_an_effect_size():
    res = stats.paired_test([2e-300, 4e-300, 3e-300, 5e-300], [1e-300] * 4)
    assert res["cohens_d"] == 1.936
    assert res["effect"] != "unknown"
