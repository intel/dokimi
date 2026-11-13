# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Critical bands enforcement — fails CI on condition violations."""

from __future__ import annotations

import math

from dokimi.gate.verdict import Check, Verdict


# The summary metric each band condition reads.
_BAND_KEYS = {
    "score_below": "score",
    "coverage_below": "coverage",
    "freshness_below": "freshness_lite",
    "readability_below": "readability",
}


def _metric(summary: dict, key: str, condition: str) -> float:
    """The summary value a condition reads. A missing key reads as 0.0; None is an error."""
    raw = summary.get(key, 0.0)
    if raw is None:
        raise ValueError(f"critical_bands {condition}: summary metric {key!r} is None, not a number")
    return float(raw)


def _below(actual: float, threshold: float) -> bool:
    """Strictly below the threshold. NaN is not a number the gate can pass, so it violates."""
    return math.isnan(actual) or actual < threshold


def _band_check(condition: str, threshold: float, actual: float, violated: bool) -> Check:
    """One band as a fail-severity check: it passes unless the metric is strictly below the threshold."""
    return Check(
        name=condition,
        passed=not violated,
        value=actual,
        threshold=threshold,
        message=f"{condition}: {actual:.4f} against the band {threshold:.4f}",
        severity="fail",
    )


def check_critical_bands(summary: dict, spec: dict) -> Verdict:
    """Check if summary violates any critical bands.

    Returns a Verdict with one check per band, named by its condition: ``passed`` is False for a violated
    band, and ``failures`` lists the violations. Caller decides whether to exit.
    Raises ValueError on unknown conditions (fail-fast on spec typos).
    """
    bands = spec.get("critical_bands", {}).get("fail_on", [])
    if not bands:
        return Verdict(enabled=False)

    checks: list[Check] = []

    for band in bands:
        if not isinstance(band, dict):
            raise ValueError("critical_bands.fail_on entries must be mappings/dicts")

        condition = band.get("condition", "")
        if not condition:
            raise ValueError("critical_bands.fail_on entry missing 'condition' key")
        if condition not in _BAND_KEYS:
            raise ValueError(f"Unknown critical_bands condition: {condition} (known: {', '.join(sorted(_BAND_KEYS))})")

        threshold = float(band.get("value", 0.0))
        actual = _metric(summary, _BAND_KEYS[condition], condition)
        checks.append(_band_check(condition, threshold, actual, _below(actual, threshold)))

    return Verdict(checks=checks)
