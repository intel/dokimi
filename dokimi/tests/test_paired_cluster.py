# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the paired, cluster-bootstrap difference used by trigger-probe and routing results."""

import pytest

from dokimi.eval.paired_cluster import paired_cluster_difference
from dokimi.eval.stats import bootstrap_ci


def test_each_cluster_counts_once_however_many_repeats_it_has():
    # Prompt "a" has three repeats that all gain; prompt "b" has one repeat that loses.
    # Per cell the mean is (3 - 1) / 4 = 0.5; per cluster it is (1 + -1) / 2 = 0.
    res = paired_cluster_difference(
        ["a", "a", "a", "b"], [True, True, True, False], [False, False, False, True], min_clusters=2,
    )
    assert res["delta"] == 0.0
    assert (res["n_clusters"], res["n_cells"], res["n_excluded"]) == (2, 4, 0)


def test_a_missing_or_non_finite_outcome_leaves_its_cell_out():
    # The second "a" cell has no treatment outcome, cluster "z" has no usable cell at all, and the
    # last cell's control is NaN. Only "a" (one cell) and "b" (one cell) remain.
    res = paired_cluster_difference(
        ["a", "a", "b", "z", "y"],
        [1, None, 1, None, 1.0],
        [0, 0, 0, 0, float("nan")],
        min_clusters=2,
    )
    assert res["delta"] == 1.0
    assert (res["n_clusters"], res["n_cells"], res["n_excluded"]) == (2, 2, 3)


def test_too_few_clusters_reports_no_interval():
    res = paired_cluster_difference(["a", "b", "c", "d"], [1, 1, 1, 1], [0, 0, 0, 0])
    assert res["status"] == "insufficient_clusters"
    assert res["min_clusters"] == 5
    assert (res["delta"], res["ci_lo"], res["ci_hi"], res["noninferiority"]) == (None, None, None, None)


def test_the_interval_resamples_clusters_so_repeats_widen_it():
    # Six prompts with four identical repeats each. Resampling the 24 cells treats the repeats as
    # independent and comes out about half as wide as resampling the six prompts.
    prompt_deltas = [1, 1, 0, 0, -1, -1]
    clusters = [p for p in range(6) for _ in range(4)]
    treatment = [d for d in prompt_deltas for _ in range(4)]
    control = [0] * 24
    res = paired_cluster_difference(clusters, treatment, control, seed=0)
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


def test_no_difference_is_noninferior_for_any_margin():
    res = paired_cluster_difference(list(range(6)), [1] * 6, [1] * 6, margin=0.05)
    assert (res["delta"], res["noninferiority"]) == (0.0, "noninferior")


def test_a_clear_loss_beyond_the_margin_is_inferior():
    res = paired_cluster_difference(list(range(6)), [0] * 6, [1] * 6, margin=0.05)
    assert (res["ci_lo"], res["ci_hi"], res["noninferiority"]) == (-1.0, -1.0, "inferior")


def test_a_point_estimate_at_zero_is_not_enough_for_noninferiority():
    # The mean difference is 0, but the interval runs from about -0.67 to +0.67: the lower bound
    # decides, so the result is inconclusive rather than noninferior.
    treatment = [1, 0, 1, 0, 1, 0]
    control = [0, 1, 0, 1, 0, 1]
    res = paired_cluster_difference(list(range(6)), treatment, control, margin=0.05)
    assert res["delta"] == 0.0
    assert res["noninferiority"] == "inconclusive"


def test_the_noninferiority_bound_is_strict():
    # A zero margin needs the lower bound above 0; a zero difference has its lower bound at 0.
    res = paired_cluster_difference(list(range(6)), [1] * 6, [1] * 6, margin=0.0)
    assert res["noninferiority"] == "inconclusive"


def test_no_margin_means_no_noninferiority_verdict():
    res = paired_cluster_difference(list(range(6)), [1] * 6, [1] * 6)
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


def test_a_negative_margin_is_rejected():
    with pytest.raises(ValueError, match="margin must be >= 0"):
        paired_cluster_difference(list(range(6)), [1] * 6, [1] * 6, margin=-0.1)


def test_an_interval_needs_at_least_two_clusters_in_its_floor():
    with pytest.raises(ValueError, match="min_clusters must be >= 2"):
        paired_cluster_difference(["a", "b"], [1, 0], [0, 1], min_clusters=1)
