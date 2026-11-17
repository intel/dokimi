# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The sign-flip interval of deltas near the float maximum: no RuntimeWarning, no nan-driven end (#725)."""

from __future__ import annotations

import math
import sys
import warnings

import pytest

from dokimi.eval.sign_flip import sign_flip_interval, sign_flip_p, sign_flip_row
from dokimi.eval.stats import _describe_deltas

BIG = sys.float_info.max
SHIFT = 25  # a power of two: scaling by it is exact, so a scaled row has the scaled row's ends
MC_BASE = [1e299 * (0.5 + (i % 7) / 14) * (1 if i % 3 else -1) for i in range(20)]  # > EXACT_MAX deltas
SIX_BASE = [1e300 * f for f in (0.6, 0.9, -0.3, 0.5, 0.7, 0.8)]
ROWS = {
    "1e308 and 0": [1e308, 0.0],
    "1.7e308 and 0": [1.7e308, 0.0],
    "max and 0": [BIG, 0.0],
    "max and -max": [BIG, -BIG],
    "six deltas": [math.ldexp(x, SHIFT) for x in SIX_BASE],
    "monte carlo": [math.ldexp(x, SHIFT) for x in MC_BASE],
}


@pytest.mark.parametrize("name", ROWS)
def test_a_row_near_the_maximum_returns_a_dict_and_warns_nothing(name):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        row = sign_flip_row(ROWS[name], exact=False)
    assert isinstance(row, dict)
    for end in (row["lo"], row["hi"]):
        assert end is None or math.isfinite(end)


@pytest.mark.parametrize("name", [n for n in ROWS if n != "max and -max"])  # its magnitudes sum past the range
def test_describe_deltas_of_a_row_near_the_maximum_warns_nothing(name):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        described = _describe_deltas(ROWS[name], confidence=0.95, seed=0)
    assert described["ci_method"] == "sign_flip_inversion"
    assert math.isfinite(described["delta_mean"])
    for end in (described["ci_lo"], described["ci_hi"]):
        assert end is None or math.isfinite(end)


@pytest.mark.parametrize("base", [SIX_BASE, MC_BASE], ids=["exact", "monte-carlo"])
def test_the_ends_of_a_scaled_row_are_the_ends_of_the_row_scaled(base):
    big = [math.ldexp(x, SHIFT) for x in base]
    small = sign_flip_interval(base, sign_flip_p(base), rounded=False)
    large = sign_flip_interval(big, sign_flip_p(big), rounded=False)
    assert all(end is not None for end in small + large)
    for got, want in zip(large, small, strict=True):
        assert got == pytest.approx(math.ldexp(want, SHIFT), rel=1e-12)


@pytest.mark.parametrize("row", [[BIG, 1.0, 1.0, 1.0, 1.0, 1.0], [-BIG, -1.0, -1.0, -1.0, -1.0, -1.0]])
def test_an_end_at_the_float_maximum_is_the_maximum(row):
    # the bisection overshoots the end by a few ulps at the maximum; multiplied back it is past the range (#770)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        for rounded in (False, True):
            ends = sign_flip_interval(row, sign_flip_p(row), rounded=rounded)
            assert (ends[1] if row[0] > 0 else ends[0]) == math.copysign(BIG, row[0])
            assert all(end is None or math.isfinite(end) for end in ends)


@pytest.mark.parametrize(("row", "ends"), [([1.7e308] * 3 + [1.5e308] * 3, ("lo", "hi")),
                                           # Test-narrowed: its lo end is the float product's noise at either scale, not a scaled value
                                           ([BIG, 1.0, 1.0, 1.0, 1.0, 1.0], ("hi",)),
                                           ([math.ldexp(x, SHIFT) for x in MC_BASE], ("lo", "hi"))],
                         ids=["six", "max and ones", "monte-carlo"])
def test_the_p_and_ends_of_a_row_near_the_maximum_are_those_of_the_row_scaled(row, ends):
    small = [math.ldexp(x, -4) for x in row]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        big_row, small_row = sign_flip_row(row, exact=False), sign_flip_row(small, exact=False)
    assert big_row["p"] == small_row["p"]
    for end in ends:
        try:
            want = None if small_row[end] is None else math.ldexp(small_row[end], 4)
        except OverflowError:
            want = math.copysign(BIG, small_row[end])  # the scaled end is past the float range: the maximum
        assert (big_row[end] is None) == (want is None)
        if want is not None:
            assert big_row[end] == pytest.approx(want, rel=1e-12)


@pytest.mark.parametrize("m", [1e15, 1e16, 1e17, 1e20, 1e300])
def test_a_delta_dwarfing_the_others_does_not_hide_them_in_the_exact_p(m):
    # benchmarking-and-comparison.md: p is the share of sign patterns whose |sum| reaches the observed one. The five
    # deltas of 1 share a sign and none ties, so only the all-same-sign pattern and its mirror reach it: 2 / 2**6 (#816)
    assert sign_flip_p([m, 1, 1, 1, 1, 1], exact=True) == 2 / 2 ** 6


def test_a_delta_dwarfing_the_others_does_not_hide_them_in_the_monte_carlo_p():
    # 18 changed deltas, above EXACT_MAX: the same draws as for 1e15, and p at its floor 1 / (N_RESAMPLES + 1) (#816)
    assert sign_flip_p([1e17] + [1.0] * 17, exact=True) == sign_flip_p([1e15] + [1.0] * 17, exact=True) == 1 / 20_001


def test_huge_deltas_that_cancel_leave_the_p_of_the_small_ones_to_the_scale_where_float_sums_are_exact():
    # +-1e17 cancel in the observed sum 6, as +-1e5 do; 1e5 + 1 is still a float sum without a rounding error (#816)
    assert sign_flip_p([1e17, -1e17] + [1] * 6, exact=True) == sign_flip_p([1e5, -1e5] + [1] * 6, exact=True) == 33 / 64
