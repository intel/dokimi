# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The sign-flip tie width follows the values of each pair (dokimi.eval.sign_flip.tie_widths), capped at EPS (issue #762)."""

from __future__ import annotations

import pytest

from dokimi.eval import stats
from dokimi.eval.sign_flip import (
    ALPHA,
    EPS,
    ShiftTest,
    pair_magnitudes,
    sign_flip_interval,
    sign_flip_p,
    sign_flip_row,
    tie_scale,
    tie_widths,
)
from dokimi.repos.compare import sign_flip_summary

GAINS = [2, 4, 3, 5, 6, 9, 7, 8]
SMALLEST = 5e-324  # the smallest subnormal float


def _pairs(treatment: list[float], baseline: list[float]) -> list[dict]:
    return [{"task": f"t{i}", "baseline": b, "treatment": t, "delta": t - b}
            for i, (t, b) in enumerate(zip(treatment, baseline, strict=True))]


def _includes_zero(lo: float | None, hi: float | None) -> bool:
    return (lo is None or lo <= 0) and (hi is None or hi >= 0)


def test_a_tie_is_eps_times_the_larger_value_of_its_pair_up_to_one_and_eps_without_values():
    assert tie_widths(None, 3) == [EPS] * 3  # deltas alone: the absolute width
    assert tie_widths([1.0, 7.0, 1e300], 3) == [EPS] * 3  # values of 1 or more: float noise near 1, as ever
    assert tie_widths([4e-12, 1e-300, 0.5], 3) == pytest.approx([EPS * 4e-12, EPS * 1e-300, EPS * 0.5], rel=1e-6, abs=0)
    # a pair of zeros, or of values that are not finite, takes the scale of the others: the vector's largest value
    assert tie_widths([0.0, 2e-12], 2) == pytest.approx([EPS * 2e-12] * 2, rel=1e-6, abs=0)
    assert tie_widths([float("nan"), float("inf"), 3e-12], 3) == pytest.approx([EPS * 3e-12] * 3, rel=1e-6, abs=0)
    assert tie_widths([0.0, float("nan")], 2) == [EPS] * 2
    assert tie_scale([1e-12, 4e-12, 2e-12]) == 4e-12 and tie_scale([0.0]) == tie_scale(None) == tie_scale([5.0]) == 1.0
    assert pair_magnitudes([0.1, -3.0], [-0.2, 1.0]) == [0.2, 3.0]


def test_deltas_that_are_float_noise_on_the_values_subtracted_are_ties_not_a_significant_change():
    # 0.1 + 0.2 and 0.3 differ in the last place: the same scores in another order, not a change
    noise = stats.paired_test([0.1 + 0.2] * 8, [0.3] * 8)
    assert (noise["p"], noise["n_changed"], noise["significant"]) == (None, 0, None)
    summary = sign_flip_summary(_pairs([0.1 + 0.2] * 8, [0.3] * 8), exact=True)
    assert (summary["up"], summary["down"], summary["tied"], summary["p"]) == (0, 0, 8, None)
    # the same rounding residue next to a score of 1 is still a tie
    assert sign_flip_p([1.1e-16] * 8, magnitudes=[1.0] * 8) is None
    # a change of the same size at the scale of its own values is not
    real = sign_flip_summary(_pairs([1.1e-16] * 8, [0.0] * 8), exact=True)
    assert (real["up"], real["down"], real["tied"]) == (8, 0, 0) and real["p"] == 0.0078125
    # a spread at the 1e-300 scale is a spread, so the row has a minimum detectable effect
    tiny = sign_flip_summary(_pairs([g * 1e-300 for g in GAINS], [1e-300] * 8), exact=True)
    assert (tiny["up"], tiny["down"], tiny["tied"]) == (8, 0, 0) and tiny["mde"] is not None


def test_deltas_at_1e_300_get_the_p_and_the_verdict_of_the_same_deltas_at_1e_3():
    tiny = stats.paired_test([g * 1e-300 for g in GAINS], [1e-300] * 8)
    usual = stats.paired_test([g * 1e-3 for g in GAINS], [1e-3] * 8)
    assert (tiny["p"], tiny["n_changed"], tiny["significant"]) == (usual["p"], usual["n_changed"], usual["significant"])
    assert (tiny["p"], tiny["n_changed"], tiny["significant"]) == (0.0078, 8, True)
    # a vector of zeros is still all ties, with no p
    zeros = stats.paired_test([0.0] * 8, [0.0] * 8)
    assert (zeros["p"], zeros["n_changed"]) == (None, 0)


def test_more_evidence_in_the_same_direction_does_not_remove_a_significant_result():
    # the width is each pair's own: a ninth delta of 1.1e-9 no longer widens the tie of the eight 5e-10
    eight = stats.paired_test([5e-10] * 8, [0.0] * 8)
    nine = stats.paired_test([5e-10] * 8 + [1.1e-9], [0.0] * 9)
    assert (eight["p"], eight["n_changed"]) == (0.0078, 8)
    assert (nine["p"], nine["n_changed"]) == (0.0039, 9)
    assert sign_flip_p([5e-10] * 8 + [1.1e-9], exact=True, magnitudes=[5e-10] * 8 + [1.1e-9]) == 2 / 2 ** 9


def test_the_interval_and_the_p_agree_at_a_tiny_scale_and_on_a_mixed_vector():
    def row_of(treatment: list[float], baseline: list[float]) -> dict:
        deltas = [t - b for t, b in zip(treatment, baseline, strict=True)]
        return sign_flip_row(deltas, exact=True, magnitudes=pair_magnitudes(treatment, baseline))

    tiny = [g * 1e-300 for g in GAINS]
    mixed = [1e-3, 2e-3, 1.5e-3, 3e-3, 2.5e-3, 1.2e-3, 1e-12]
    small = [-1e-12, 1e-12, 3e-12, 2e-12, 5e-13, 4e-12, -2e-12]
    for treatment in (tiny, mixed, small):
        row = row_of(treatment, [0.0] * len(treatment))
        assert row["p"] is not None
        assert _includes_zero(row["lo"], row["hi"]) == (row["p"] >= ALPHA), treatment
    assert row_of(tiny, [0.0] * 8)["n_changed"] == 8
    # the 1e-12 beside 1e-3 values is a change of its own (width 1e-21), as a pair whose values are that small
    assert row_of(mixed, [0.0] * 7)["n_changed"] == 7
    # the ends are step points of the one p(theta) of the original vector, at its own scale
    magnitudes = pair_magnitudes(tiny, [0.0] * 8)
    lo, hi = sign_flip_interval(tiny, sign_flip_p(tiny, magnitudes=magnitudes), rounded=False, magnitudes=magnitudes)
    test = ShiftTest(tiny, eps=tie_widths(magnitudes, 8))
    step = 1e-6 * 1e-300
    assert 0 < lo < hi
    assert test.p(lo) >= ALPHA > test.p(lo - step) and test.p(hi) >= ALPHA > test.p(hi + step)


def test_deltas_of_the_smallest_floats_have_an_interval_that_excludes_zero_with_their_significant_p():
    treatment = [SMALLEST] * 6 + [0.0] * 7
    row = sign_flip_row(treatment, exact=True, magnitudes=pair_magnitudes(treatment, [0.0] * 13))
    assert row["n_changed"] == 6 and row["p"] == 0.03125 < ALPHA
    assert not _includes_zero(row["lo"], row["hi"])  # the ends are not rounded onto 0 by the float range
    assert 0 < row["lo"] <= row["hi"] <= 1e-322
    # 6 changed pairs of -SMALLEST are the mirror image: both ends below 0
    mirror = [-x for x in treatment]
    row = sign_flip_row(mirror, exact=True, magnitudes=pair_magnitudes(mirror, [0.0] * 13))
    assert row["hi"] < 0 and row["lo"] <= row["hi"]
    # and the arms track reports the same significant row, not a p beside a zero-width interval
    result = stats.paired_test(treatment, [0.0] * 13)
    assert (result["p"], result["n_changed"], result["significant"]) == (0.0312, 6, True)
    assert result["exact"]["ci_lo"] > 0


def test_the_shifted_deltas_keep_the_tie_widths_of_the_original_pairs():
    # shifted by 1.0 the deltas are 1e-10 off 0, and 1e-10 is a tie of the original pairs (values near 1)
    d = [1.0, 1.0 + 1e-10, 1.0 - 1e-10, 1.0 + 2e-10, 1.0, 1.0]
    assert ShiftTest(d, eps=tie_widths([1.0] * 6, 6)).p(1.0) is None
    # a width per delta: at a shift of 1.0 the last delta is 1e-10 off 0, a change under its width of 0, a tie under EPS
    narrow = ShiftTest([1.0, 1.0, 1.0 + 1e-10], eps=[EPS, EPS, 0.0])
    assert narrow.changed == 3 and narrow.p(1.0) == 2 / 2
    assert ShiftTest([1.0, 1.0, 1.0 + 1e-10], eps=EPS).p(1.0) is None


def test_deltas_alone_keep_the_absolute_tie_width():
    assert sign_flip_p([0.0, 0.0, 1e-12]) is None
    assert sign_flip_row([1e-300] * 8, exact=True)["n_changed"] == 0


def test_an_unchanged_pair_of_large_values_does_not_loosen_the_sums_of_the_small_changed_pairs():
    treatment, baseline = [1e-12] * 8 + [1.0], [0.0] * 8 + [1.0]
    deltas, mags = [t - b for t, b in zip(treatment, baseline, strict=True)], pair_magnitudes(treatment, baseline)
    alone = sign_flip_p(deltas[:8], magnitudes=mags[:8], exact=True)
    assert alone == 0.0078125 and sign_flip_p(deltas, magnitudes=mags, exact=True) == alone
    assert stats.paired_test(treatment, baseline)["significant"] is True
    lo, hi = sign_flip_interval(deltas, alone, rounded=False, magnitudes=mags)
    assert lo is not None and hi is not None and 0 < lo <= hi  # p(0) is below ALPHA: the interval excludes 0
    # the shifted test: the unchanged pair is a tie at theta 0, and the sums of the 8 small deltas have the small slack
    assert ShiftTest(deltas, eps=tie_widths(mags, 9)).p(0.0) == alone
