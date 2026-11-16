# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Paired difference of two arms over clustered units, with a cluster-bootstrap interval.

Trigger-probe and routing results pair the same prompt under two arms, and prompts repeat: several
repeats of one prompt, or several prompts of one task, share that unit's difficulty. A cell-level
interval treats every repeat as an independent pair and comes out too narrow. This module resamples
the clusters instead (the caveat :mod:`dokimi.eval.binary_stats` states for McNemar, which does not
resample clusters).

Each cell is one paired observation: the outcome under ``treatment`` and under ``control`` for the
same prompt or repeat, with the cluster (prompt id or task id) it belongs to. Within a cluster each
arm is averaged over its usable cells, and the cluster's delta is ``mean(treatment) - mean(control)``,
so a cluster counts once however many repeats it has, the order of the repeats does not matter, and
a missing outcome on one side does not discard the other side's. The interval is the percentile bootstrap of that cluster mean
(:func:`dokimi.eval.stats.bootstrap_ci` on the per-cluster deltas), deterministic for a ``seed``.

Non-inferiority: the treatment is non-inferior when the lower bound of the interval is above
``-margin``, the largest drop the caller accepts, fixed before the run and on the outcome's scale
(0..1 for pass rates). The nominal lower bound of a two-sided 95% interval corresponds to the
one-sided 97.5% bound non-inferiority trials use, but the percentile bootstrap is too narrow with
few clusters (see :func:`dokimi.eval.stats.bootstrap_ci`): simulated at a true delta equal to
``-margin`` (binary cells, one per cluster, p between 0.5 and 0.9), it reads ``noninferior``
roughly 2-8% of the time at 10 clusters, about 15% at 5-6 and about 2% at 20. :data:`MIN_CLUSTERS`
is a floor below which no interval is reported, not a power calculation, and a ``noninferior``
verdict near the floor is a screening signal, not a trial-grade claim. An interval of zero width
(every cluster has the same delta) carries no spread estimate: it never reads
``noninferior`` (``inferior`` when it lies below ``-margin``, else ``inconclusive``).
"""

from __future__ import annotations

import math
from collections.abc import Hashable, Sequence
from typing import Any, Dict, List, Optional, Tuple

from dokimi.eval.stats import bootstrap_ci

#: Fewest clusters with a usable pair before an interval is reported at all.
MIN_CLUSTERS = 10

#: Bootstrap resamples; the same default as :func:`dokimi.eval.stats.bootstrap_ci`.
DEFAULT_RESAMPLES = 10_000

#: Tolerance for comparing a bound with ``-margin``, so float noise does not flip a verdict.
_EPS = 1e-9

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
    or repeat answered under both arms. A missing (``None``) or non-finite outcome is left out of its
    arm's cluster mean and counted in ``n_excluded``; a cluster needs a usable outcome in each arm to
    count toward ``n_clusters``. ``None`` means "not run": score a failed run as 0, not ``None``, or
    it drops out instead of counting against its arm.

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
        ``inconclusive`` (a zero-width interval is never ``noninferior``); ``None`` without a margin or when the
        interval is not measured. ``margin=0`` makes it a superiority check.
    ``n_clusters`` / ``n_cells`` / ``n_excluded``
        Clusters with a usable outcome in both arms, usable outcomes in those clusters (counted
        per arm, so a complete paired cell counts 2), and outcomes left out (missing, non-finite, or in a cluster missing one arm).
    ``unit`` / ``margin`` / ``ci_confidence`` / ``seed`` / ``method``
        Echo of the inputs; ``min_clusters`` too when the status is ``insufficient_clusters``.
    """
    _validate(clusters, treatment, control, margin, confidence, n_resamples, min_clusters)
    cluster_deltas, n_cells, n_excluded = _cluster_deltas(clusters, treatment, control)
    out: Dict[str, Any] = {
        "unit": "cluster",
        "n_clusters": len(cluster_deltas),
        "n_cells": n_cells,
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
    out["noninferiority"] = _verdict(est["lo"], est["hi"], margin)
    return out


def _validate(
    clusters: Sequence[Hashable],
    treatment: Sequence[Optional[float]],
    control: Sequence[Optional[float]],
    margin: Optional[float],
    confidence: float,
    n_resamples: int,
    min_clusters: int,
) -> None:
    if isinstance(clusters, (str, bytes)):
        raise TypeError("clusters must be a sequence of ids, not a string")
    if len(clusters) != len(treatment) or len(clusters) != len(control):
        raise ValueError(
            "clusters, treatment and control must be one per cell: "
            f"got {len(clusters)}, {len(treatment)} and {len(control)}"
        )
    if margin is not None and not (math.isfinite(margin) and margin >= 0):
        raise ValueError(f"margin must be finite and >= 0 (the largest accepted drop), got {margin}")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if n_resamples < 100:
        raise ValueError(f"n_resamples must be >= 100, got {n_resamples}")
    if min_clusters < 2:
        raise ValueError(f"min_clusters must be >= 2 for an interval, got {min_clusters}")


def _cluster_deltas(
    clusters: Sequence[Hashable],
    treatment: Sequence[Optional[float]],
    control: Sequence[Optional[float]],
) -> Tuple[List[float], int, int]:
    """Per-cluster ``mean(treatment) - mean(control)``, usable outcomes, and outcomes left out."""
    arms: Dict[Hashable, Tuple[List[float], List[float]]] = {}
    n_excluded = 0
    for cluster, t, c in zip(clusters, treatment, control, strict=True):
        # A NaN of any type (float, Decimal, numpy) is the one value unequal to itself.
        if cluster is None or cluster != cluster:
            raise ValueError("cluster ids must not be None or NaN")
        t_list, c_list = arms.setdefault(cluster, ([], []))
        # Booleans pass as 0/1; a missing or non-finite outcome leaves only that side's value out.
        for value, bucket in ((t, t_list), (c, c_list)):
            if value is None or not math.isfinite(value):
                n_excluded += 1
            else:
                bucket.append(float(value))

    cluster_deltas: List[float] = []
    n_cells = 0
    for t_list, c_list in arms.values():
        if t_list and c_list:
            cluster_deltas.append(sum(t_list) / len(t_list) - sum(c_list) / len(c_list))
            n_cells += len(t_list) + len(c_list)
        else:
            n_excluded += len(t_list) + len(c_list)
    return cluster_deltas, n_cells, n_excluded


def _verdict(lo: float, hi: float, margin: Optional[float]) -> Optional[str]:
    """Non-inferiority status of the interval ``[lo, hi]`` against ``-margin``."""
    if margin is None:
        return None
    if hi < -margin - _EPS:
        return "inferior"
    # A zero-width interval has no spread estimate, so it cannot show non-inferiority.
    if lo > -margin + _EPS and hi - lo > _EPS:
        return "noninferior"
    return "inconclusive"
