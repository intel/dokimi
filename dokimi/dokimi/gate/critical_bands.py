# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Critical bands enforcement — fails CI on condition violations."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class BandViolation:
    """Single critical band violation."""

    condition: str
    threshold: float
    actual: float
    violated: bool


@dataclass
class CriticalBandsResult:
    """Critical bands check result."""

    enabled: bool
    violations: list[BandViolation]

    @property
    def passed(self) -> bool:
        """True if no violations."""
        return not self.has_violations

    @property
    def has_violations(self) -> bool:
        """Any critical band violated."""
        return any(v.violated for v in self.violations)


def _metric(summary: dict, key: str, condition: str) -> float:
    """The summary value a condition reads. A missing key reads as 0.0; None is an error."""
    raw = summary.get(key, 0.0)
    if raw is None:
        raise ValueError(f"critical_bands {condition}: summary metric {key!r} is None, not a number")
    return float(raw)


def _below(actual: float, threshold: float) -> bool:
    """Strictly below the threshold. NaN is not a number the gate can pass, so it violates."""
    return math.isnan(actual) or actual < threshold


def check_critical_bands(summary: dict, spec: dict) -> CriticalBandsResult:
    """Check if summary violates any critical bands.
    
    Returns CriticalBandsResult. Caller decides whether to exit.
    Raises ValueError on unknown conditions (fail-fast on spec typos).
    """
    bands = spec.get("critical_bands", {}).get("fail_on", [])
    if not bands:
        return CriticalBandsResult(enabled=False, violations=[])

    violations: list[BandViolation] = []
    known_conditions = {"score_below", "coverage_below", "freshness_below", "readability_below"}

    for band in bands:
        if not isinstance(band, dict):
            raise ValueError("critical_bands.fail_on entries must be mappings/dicts")

        condition = band.get("condition", "")
        if not condition:
            raise ValueError("critical_bands.fail_on entry missing 'condition' key")
        if condition not in known_conditions:
            raise ValueError(f"Unknown critical_bands condition: {condition} (known: {', '.join(sorted(known_conditions))})")

        threshold = float(band.get("value", 0.0))

        if condition == "score_below":
            actual = _metric(summary, "score", "score_below")
            violated = _below(actual, threshold)
            violations.append(BandViolation(condition, threshold, actual, violated))
        elif condition == "coverage_below":
            actual = _metric(summary, "coverage", "coverage_below")
            violated = _below(actual, threshold)
            violations.append(BandViolation(condition, threshold, actual, violated))
        elif condition == "freshness_below":
            actual = _metric(summary, "freshness_lite", "freshness_below")
            violated = _below(actual, threshold)
            violations.append(BandViolation(condition, threshold, actual, violated))
        elif condition == "readability_below":
            actual = _metric(summary, "readability", "readability_below")
            violated = _below(actual, threshold)
            violations.append(BandViolation(condition, threshold, actual, violated))

    return CriticalBandsResult(enabled=True, violations=violations)
