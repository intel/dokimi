# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Paired difference of two arms over clustered units, with a cluster-bootstrap interval.

Trigger-probe and routing results pair the same prompt under two arms, and prompts repeat: several
repeats of one prompt, or several prompts of one task, share that unit's difficulty. A cell-level
interval treats every repeat as an independent pair and comes out too narrow. This module resamples
the clusters instead (the caveat :mod:`dokimi.eval.binary_stats` states for McNemar, which does not
resample clusters).

Each cell is one paired observation: the outcome under ``treatment`` and under ``control`` for the
same prompt or repeat, with the cluster (prompt id or task id) it belongs to. The estimate is the
mean over clusters of each cluster's mean paired difference, so a cluster counts once however many
repeats it has. The interval is the percentile bootstrap of that cluster mean
(:func:`dokimi.eval.stats.bootstrap_ci` on the per-cluster deltas), deterministic for a ``seed``.

Non-inferiority: the treatment is non-inferior when the lower bound of the interval is above
``-margin``, the largest drop the caller accepts, fixed before the run and on the outcome's scale
(0..1 for pass rates). The lower bound of a two-sided 95% interval is the one-sided 97.5% bound
that non-inferiority trials conventionally use. A p-value above 0.05 does not show equality; the
interval does.

The percentile interval is too narrow with few clusters (see :func:`dokimi.eval.stats.bootstrap_ci`),
so :data:`MIN_CLUSTERS` is a floor below which no interval is reported, not a power calculation.
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from typing import Any, Dict, List, Optional

from dokimi.eval.stats import bootstrap_ci

#: Fewest clusters with a usable pair before an interval is reported at all.
MIN_CLUSTERS = 5

#: Bootstrap resamples; the same default as :func:`dokimi.eval.stats.bootstrap_ci`.
DEFAULT_RESAMPLES = 10_000

#: Display precision of the delta and its bounds; the status is decided on unrounded values.
DISPLAY_DIGITS = 4


def paired_cluster_difference(
    clusters: Sequence[Hashable],
    treatment: Sequence[Optional[float]],
    control: Sequence[Optional[float]],
    *,
    margin: Optional[float] = None,
    confidence: float = 0.95,
    n_resamples: int = DEFAULT_RESAMPLES,
    seed: int = 0,
    min_clusters: int = MIN_CLUSTERS,
) -> Dict[str, Any]:
    """Mean paired difference ``treatment - control`` over clusters, with a cluster-bootstrap CI.

    ``clusters[i]``, ``treatment[i]`` and ``control[i]`` describe one paired cell: the same prompt
    or repeat answered under both arms. A cell with a missing (``None``) or non-finite outcome on
    either side is left out and counted in ``n_excluded``; a cluster left with no usable cell does
    not count toward ``n_clusters``.

    ``margin``, when given, sets the non-inferiority check. ``seed`` fixes the bootstrap, so the same
    inputs and seed reproduce the interval exactly.

    Keys:

    ``status``
        ``measured``, or ``insufficient_clusters`` when fewer than ``min_clusters`` clusters are
        usable; then ``delta``, ``ci_lo`` and ``ci_hi`` are ``None``.
    ``delta`` / ``ci_lo`` / ``ci_hi``
        Mean over clusters of the per-cluster mean difference, and its percentile interval at
        ``ci_confidence``.
    ``noninferiority``
        ``noninferior`` when ``ci_lo > -margin``, ``inferior`` when ``ci_hi < -margin``, else
        ``inconclusive``; ``None`` without a margin or when the interval is not measured.
    ``n_clusters`` / ``n_cells`` / ``n_excluded``
        Clusters with a usable cell, usable cells, and cells left out.
    """
    if len(clusters) != len(treatment) or len(clusters) != len(control):
        raise ValueError(
            "clusters, treatment and control must be one per cell: "
            f"got {len(clusters)}, {len(treatment)} and {len(control)}"
        )
    if margin is not None and margin < 0:
        raise ValueError(f"margin must be >= 0 (the largest accepted drop), got {margin}")
    if min_clusters < 2:
        raise ValueError(f"min_clusters must be >= 2 for an interval, got {min_clusters}")

    per_cluster: Dict[Hashable, List[float]] = {}
    n_excluded = 0
    for cluster, t, c in zip(clusters, treatment, control, strict=True):
        # Booleans pass as 0/1; a missing or non-finite outcome leaves the cell out of the pairing.
        if t is None or c is None or not (math.isfinite(t) and math.isfinite(c)):
            n_excluded += 1
            continue
        per_cluster.setdefault(cluster, []).append(t - c)

    cluster_deltas = [sum(cells) / len(cells) for cells in per_cluster.values()]
    out: Dict[str, Any] = {
        "unit": "cluster",
        "n_clusters": len(cluster_deltas),
        "n_cells": sum(len(cells) for cells in per_cluster.values()),
        "n_excluded": n_excluded,
        "margin": margin,
        "ci_confidence": confidence,
        "seed": seed,
        "method": f"cluster bootstrap over clusters, {n_resamples} resamples",
        "delta": None,
        "ci_lo": None,
        "ci_hi": None,
        "noninferiority": None,
    }
    if len(cluster_deltas) < min_clusters:
        out["status"] = "insufficient_clusters"
        out["min_clusters"] = min_clusters
        return out

    est = bootstrap_ci(
        cluster_deltas, confidence=confidence, n_resamples=n_resamples, seed=seed, ndigits=None,
    )
    out["status"] = "measured"
    out["delta"] = round(est["mean"], DISPLAY_DIGITS)
    out["ci_lo"] = round(est["lo"], DISPLAY_DIGITS)
    out["ci_hi"] = round(est["hi"], DISPLAY_DIGITS)
    if margin is not None:
        if est["lo"] > -margin:
            out["noninferiority"] = "noninferior"
        elif est["hi"] < -margin:
            out["noninferiority"] = "inferior"
        else:
            out["noninferiority"] = "inconclusive"
    return out
