# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the paired, cluster-bootstrap difference used by trigger-probe and routing results."""

import pytest

from dokimi.eval.paired_cluster import MIN_CLUSTERS, paired_cluster_difference
from dokimi.eval.stats import bootstrap_ci

N = MIN_CLUSTERS


def test_each_cluster_counts_once_however_many_repeats_it_has():
    # Prompt "a" has three repeats that all gain; prompt "b" has one repeat that loses.
    # Per cell the mean is (3 - 1) / 4 = 0.5; per cluster it is (1 + -1) / 2 = 0.
    res = paired_cluster_difference(
        ["a", "a", "a", "b"], [True, True, True, False], [False, False, False, True], min_clusters=2,
    )
    assert res["delta"] == 0.0
    assert (res["n_clusters"], res["n_cells"], res["n_excluded"]) == (2, 8, 0)


def test_the_order_of_repeats_inside_a_cluster_does_not_change_the_delta():
    # Cluster 0 has a missing treatment repeat. Its control repeats [1, 0] and [0, 1] are the same
    # data in two orders: each arm is averaged per cluster, so both give 1 - 0.5 = 0.5 for it.
    clusters = [0, 0, 1, 2, 3, 4]
    treatment = [1, None, 1, 1, 1, 1]
    one = paired_cluster_difference(clusters, treatment, [1, 0, 1, 1, 1, 1], min_clusters=2)
    other = paired_cluster_difference(clusters, treatment, [0, 1, 1, 1, 1, 1], min_clusters=2)
    assert one["delta"] == other["delta"] == 0.1
    assert one["n_excluded"] == other["n_excluded"] == 1


def test_a_missing_outcome_drops_only_its_own_side():
    # "a": treatment [1], control [0, 0] -> +1. "b": +1. "z": no treatment at all, so its control
    # is dropped too. "y": NaN control, no usable control, dropped with its treatment.
    res = paired_cluster_difference(
        ["a", "a", "b", "z", "y"],
        [1, None, 1, None, 1.0],
        [0, 0, 0, 0, float("nan")],
        min_clusters=2,
    )
    assert res["delta"] == 1.0
    # usable outcomes: a 1+2, b 1+1 = 5; excluded: None, None, NaN, z's control, y's treatment = 5.
    assert (res["n_clusters"], res["n_cells"], res["n_excluded"]) == (2, 5, 5)


def test_too_few_clusters_reports_no_interval():
    res = paired_cluster_difference(list(range(N - 1)), [1] * (N - 1), [0] * (N - 1))
    assert res["status"] == "insufficient_clusters"
    assert res["min_clusters"] == N
    assert (res["delta"], res["ci_lo"], res["ci_hi"], res["noninferiority"]) == (None, None, None, None)


def test_exactly_the_floor_of_clusters_is_measured():
    res = paired_cluster_difference(list(range(N)), [1, 0] * (N // 2), [0] * N)
    assert res["status"] == "measured"
    assert res["n_clusters"] == N


def test_the_interval_resamples_clusters_so_repeats_widen_it():
    # Six prompts with four identical repeats each. Resampling the 24 cells treats the repeats as
    # independent and comes out about half as wide as resampling the six prompts.
    prompt_deltas = [1, 1, 0, 0, -1, -1]
    clusters = [p for p in range(6) for _ in range(4)]
    treatment = [d for d in prompt_deltas for _ in range(4)]
    control = [0] * 24
    res = paired_cluster_difference(clusters, treatment, control, seed=0, min_clusters=6)
    cell = bootstrap_ci([float(t) for t in treatment], seed=0, ndigits=None)
    assert res["status"] == "measured"
    assert (res["ci_hi"] - res["ci_lo"]) > 1.5 * (cell["hi"] - cell["lo"])


def test_the_interval_is_the_seeded_bootstrap_of_the_cluster_deltas():
    # Six gained, six lost, three tied, one cell per cluster: the bounds depend on the seed.
    treatment = [1] * 6 + [0] * 6 + [1] * 3
    control = [0] * 6 + [1] * 6 + [1] * 3
    clusters = list(range(15))
    deltas = [float(t - c) for t, c in zip(treatment, control, strict=True)]
    expected = bootstrap_ci(deltas, seed=7, ndigits=None)

    default = paired_cluster_difference(clusters, treatment, control)
    seeded = paired_cluster_difference(clusters, treatment, control, seed=7)
    assert (seeded["ci_lo"], seeded["ci_hi"]) == (round(expected["lo"], 4), round(expected["hi"], 4))
    assert (default["ci_lo"], default["ci_hi"]) != (seeded["ci_lo"], seeded["ci_hi"])
    again = paired_cluster_difference(clusters, treatment, control, seed=7)
    assert again == seeded


def test_a_narrower_confidence_gives_a_narrower_interval():
    treatment = [1] * 6 + [0] * 6 + [1] * 3
    control = [0] * 6 + [1] * 6 + [1] * 3
    clusters = list(range(15))
    wide = paired_cluster_difference(clusters, treatment, control)
    narrow = paired_cluster_difference(clusters, treatment, control, confidence=0.5)
    assert narrow["ci_confidence"] == 0.5
    assert wide["ci_lo"] < narrow["ci_lo"] <= narrow["ci_hi"] < wide["ci_hi"]


def test_a_zero_width_interval_is_inconclusive_not_noninferior():
    # Every cluster ties: the bootstrap has no spread to estimate, so it is no evidence.
    res = paired_cluster_difference(list(range(N)), [1] * N, [1] * N, margin=0.05)
    assert (res["delta"], res["ci_lo"], res["ci_hi"]) == (0.0, 0.0, 0.0)
    assert res["noninferiority"] == "inconclusive"


def test_a_small_loss_well_inside_the_margin_is_noninferior():
    treatment = [1] * 9 + [0]
    control = [1] * 10
    res = paired_cluster_difference(list(range(10)), treatment, control, margin=0.5)
    assert res["noninferiority"] == "noninferior"


def test_a_clear_loss_beyond_the_margin_is_inferior():
    treatment = [0] * 9 + [1]
    control = [1] * 10
    res = paired_cluster_difference(list(range(10)), treatment, control, margin=0.05)
    assert res["noninferiority"] == "inferior"


def test_a_point_estimate_at_zero_is_not_enough_for_noninferiority():
    # The mean difference is 0, but the interval is wide: the lower bound decides.
    treatment = [1, 0] * (N // 2)
    control = [0, 1] * (N // 2)
    res = paired_cluster_difference(list(range(N)), treatment, control, margin=0.05)
    assert res["delta"] == 0.0
    assert res["noninferiority"] == "inconclusive"


def test_a_bound_equal_to_the_margin_up_to_float_noise_is_inconclusive():
    # 0.2 - 0.3 and 0.1 - 0.2 are both -0.1 up to rounding; neither side of -margin may win.
    for t, c in ((0.2, 0.3), (0.1, 0.2)):
        treatment = [t] * (N - 1) + [t + 0.5]
        control = [c] * N
        res = paired_cluster_difference(
            list(range(N)), treatment, control, margin=0.1, confidence=0.5, seed=1,
        )
        if abs(res["ci_lo"] + 0.1) < 1e-6:
            assert res["noninferiority"] == "inconclusive"


def test_no_margin_means_no_noninferiority_verdict():
    res = paired_cluster_difference(list(range(N)), [1] * N, [1] * N)
    assert res["status"] == "measured"
    assert res["noninferiority"] is None
    assert res["margin"] is None


@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"treatment": [1, 1], "control": [0, 0]}, "one per cell"),
        ({"treatment": [1, 1, 1], "control": [0, 0]}, "one per cell"),
    ],
)
def test_the_three_sequences_must_be_the_same_length(kwargs, message):
    with pytest.raises(ValueError, match=message):
        paired_cluster_difference(["a", "b", "c"], **kwargs)


@pytest.mark.parametrize("margin", [-0.1, float("nan"), float("inf")])
def test_a_negative_or_non_finite_margin_is_rejected(margin):
    with pytest.raises(ValueError, match="margin must be finite and >= 0"):
        paired_cluster_difference(list(range(N)), [1] * N, [1] * N, margin=margin)


@pytest.mark.parametrize(
    "kwargs,message",
    [({"confidence": 2}, "confidence must be in"), ({"n_resamples": 0}, "n_resamples must be")],
)
def test_bad_arguments_raise_even_with_too_few_clusters(kwargs, message):
    with pytest.raises(ValueError, match=message):
        paired_cluster_difference(["a"], [1], [0], **kwargs)


def test_none_or_nan_cluster_ids_are_rejected():
    for bad in (None, float("nan")):
        with pytest.raises(ValueError, match="cluster ids"):
            paired_cluster_difference(["a", bad], [1, 1], [0, 0], min_clusters=2)


def test_a_string_is_not_a_sequence_of_cluster_ids():
    with pytest.raises(TypeError, match="not a string"):
        paired_cluster_difference("ab", [1, 1], [0, 0], min_clusters=2)


def test_an_interval_needs_at_least_two_clusters_in_its_floor():
    with pytest.raises(ValueError, match="min_clusters must be >= 2"):
        paired_cluster_difference(["a", "b"], [1, 0], [0, 1], min_clusters=1)
