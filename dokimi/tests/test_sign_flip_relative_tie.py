# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The sign-flip tie width is the delta vector's (dokimi.eval.sign_flip.tie_tolerance), capped at EPS (issue #762)."""

from __future__ import annotations

import pytest

from dokimi.eval import stats
from dokimi.eval.paired_cluster import paired_cluster_difference
from dokimi.eval.sign_flip import ALPHA, EPS, ShiftTest, sign_flip_interval, sign_flip_p, sign_flip_row, tie_tolerance
from dokimi.repos.compare import headroom, sign_flip_summary

GAINS = [2, 4, 3, 5, 6, 9, 7, 8]


def _pairs(deltas: list[float]) -> list[dict]:
    return [{"task": f"t{i}", "baseline": 0.0, "treatment": d, "delta": d} for i, d in enumerate(deltas)]


def _includes_zero(lo: float | None, hi: float | None) -> bool:
    return (lo is None or lo <= 0) and (hi is None or hi >= 0)


def test_tie_tolerance_is_eps_from_a_largest_delta_of_eps_up_and_scales_below_it():
    assert tie_tolerance([1.0, 1e-12]) == tie_tolerance([0.5, 0.2]) == tie_tolerance([EPS, 0.0]) == EPS
    assert tie_tolerance([1e-12, -4e-12, 2e-12]) == pytest.approx(EPS * 4e-12)
    assert tie_tolerance([1e-300]) == pytest.approx(EPS * 1e-300)
    # nothing to scale by: every delta of such a vector is a tie under EPS, as before
    assert tie_tolerance([0.0, 0.0]) == tie_tolerance([]) == tie_tolerance([float("nan"), float("inf")]) == EPS
    assert tie_tolerance([3e-12, float("inf"), float("nan")]) == pytest.approx(EPS * 3e-12)  # non-finite: not a scale


def test_deltas_at_1e_300_get_the_p_and_the_verdict_of_the_same_deltas_at_1e_3():
    tiny = stats.paired_test([g * 1e-300 for g in GAINS], [1e-300] * 8)
    usual = stats.paired_test([g * 1e-3 for g in GAINS], [1e-3] * 8)
    assert (tiny["p"], tiny["n_changed"], tiny["significant"]) == (usual["p"], usual["n_changed"], usual["significant"])
    assert (tiny["p"], tiny["n_changed"], tiny["significant"]) == (0.0078, 8, True)


def test_the_interval_and_the_p_agree_at_a_tiny_scale_and_on_a_mixed_vector():
    tiny = [g * 1e-300 for g in GAINS]
    mixed = [1e-3, 2e-3, 1.5e-3, 3e-3, 2.5e-3, 1.2e-3, 1e-12]  # the 1e-12 stays a tie under the 1e-9 cap
    small = [-1e-12, 1e-12, 3e-12, 2e-12, 5e-13, 4e-12, -2e-12]
    for deltas in (tiny, mixed, small):
        row = sign_flip_row(deltas, exact=True)
        assert row["p"] is not None
        assert _includes_zero(row["lo"], row["hi"]) == (row["p"] >= ALPHA), deltas
    assert sign_flip_row(mixed, exact=True)["n_changed"] == 6
    assert sign_flip_row(tiny, exact=True)["n_changed"] == 8
    # the ends are step points of the one p(theta) of the original vector, at its own scale
    lo, hi = sign_flip_interval(tiny, sign_flip_p(tiny), rounded=False)
    test = ShiftTest(tiny)
    step = 1e-6 * 1e-300
    assert 0 < lo < hi
    assert test.p(lo) >= ALPHA > test.p(lo - step) and test.p(hi) >= ALPHA > test.p(hi + step)


def test_the_shifted_deltas_keep_the_tolerance_of_the_original_vector():
    # shifted by 1.0 the deltas are 1e-10 off 0, and 1e-10 is a tie of the original (largest 1.0002): no p, not a p
    # of a vector that would be read at its own 1e-10 scale
    d = [1.0, 1.0 + 1e-10, 1.0 - 1e-10, 1.0 + 2e-10, 1.0, 1.0]
    assert ShiftTest(d).p(1.0) is None
    assert sign_flip_p([x - 1.0 for x in d]) is not None  # the vector of the shifted values alone is another scale


def test_a_one_e_minus_12_scale_vector_is_not_all_ties_but_zeros_still_are():
    assert sign_flip_row([0.0] * 8, exact=True)["p"] is None
    assert sign_flip_row([0.0] * 8, exact=True)["n_changed"] == 0
    small = sign_flip_row([g * 1e-12 for g in GAINS], exact=True)
    usual = sign_flip_row([g * 1e-3 for g in GAINS], exact=True)
    assert (small["p"], small["n_changed"]) == (usual["p"], usual["n_changed"]) == (0.0078125, 8)


def test_repo_compare_counts_up_down_and_tied_at_the_scale_of_the_deltas():
    tiny = sign_flip_summary(_pairs([g * 1e-300 for g in GAINS]), exact=True)
    assert (tiny["up"], tiny["down"], tiny["tied"]) == (8, 0, 0)
    assert tiny["p"] == 0.0078125 and tiny["mde"] is not None
    mixed = sign_flip_summary(_pairs([1e-3] * 6 + [1e-12, -1e-12]))
    assert (mixed["up"], mixed["down"], mixed["tied"]) == (6, 0, 2)
    zeros = sign_flip_summary(_pairs([0.0] * 8))
    assert (zeros["up"], zeros["down"], zeros["tied"], zeros["p"]) == (0, 0, 8, None)


def test_headroom_reads_a_floor_only_in_zeros_not_in_values_that_vary_at_a_tiny_scale():
    def pairs(baseline: float, treatment: float) -> list[dict]:
        return [{"baseline": baseline, "treatment": treatment} for _ in range(3)]

    tops = [(1.0, 1.0)] * 3
    assert headroom(pairs(0.0, 0.0), tops) == "floor"
    assert headroom(pairs(1e-12, 1e-12), tops) is None


def test_the_non_inferiority_slack_scales_down_with_bounds_that_are_all_below_it():
    clusters = [f"c{i}" for i in range(12)]
    control = [0.0] * 12
    gain = [1e-12 * (1 + i / 10) for i in range(12)]
    assert paired_cluster_difference(clusters, gain, control, margin=0.0)["noninferiority"] == "noninferior"
    loss = [-g for g in gain]
    assert paired_cluster_difference(clusters, loss, control, margin=0.0)["noninferiority"] == "inferior"
    # at score scale the 1e-9 slack is kept: a lower bound within it of -margin is not a win
    score = [0.0] * 11 + [1.0]
    assert paired_cluster_difference(clusters, score, control, margin=0.0)["noninferiority"] == "inconclusive"
