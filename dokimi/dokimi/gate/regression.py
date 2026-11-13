# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Regression detection and classification."""

from __future__ import annotations

from typing import Literal

from dokimi.gate.verdict import Check, Verdict

Severity = Literal["OK", "WARN", "CRITICAL"]


def _classify_regression(
    delta: float, warn_threshold: float, critical_threshold: float
) -> Severity:
    """Classify a negative delta by threshold.

    Delta is expected to be negative for regressions.
    Thresholds are positive (how much drop is allowed).
    """
    if delta >= 0:
        return "OK"  # Improvement, not regression
    drop = abs(delta)
    if drop >= critical_threshold:
        return "CRITICAL"
    if drop >= warn_threshold:
        return "WARN"
    return "OK"


def _regression_check(
    name: str, delta: float, warn: float, critical: float, *, measured: bool = True
) -> Check:
    """One metric as a graded check: ``level`` is its OK/WARN/CRITICAL grade, ``threshold`` the
    ``(warn, critical)`` drops it is graded against. Regressions report: a CRITICAL one is a fail-severity
    check, which ``compare`` does not act on.
    ``measured`` is False when the diff did not carry the metric."""
    level = _classify_regression(delta, warn, critical)
    return Check(
        name=name,
        passed=level == "OK",
        value=delta,
        threshold=(warn, critical),
        message=f"{name} delta {delta:+.4f} graded {level}",
        # CRITICAL is a fail-severity check, so the verdict's status and flags read it as FAIL
        severity="fail" if level == "CRITICAL" else "warn",
        level=level,
        measured=measured,
    )


def detect_regressions(diff: dict, spec: dict) -> Verdict:
    """Analyze compare diff for regressions.

    Returns a Verdict whose first check is ``score``, followed by one check per metric present in the diff.
    """
    thresholds = spec.get("thresholds", {}).get("regressions", {})
    score_warn = float(thresholds.get("score_drop_warn", 0.03))
    score_crit = float(thresholds.get("score_drop_critical", 0.08))
    metric_warn = float(thresholds.get("metric_drop_warn", 0.05))
    metric_crit = float(thresholds.get("metric_drop_critical", 0.12))

    checks = [_regression_check("score", float(diff.get("score", 0.0)), score_warn, score_crit,
                                measured="score" in diff)]

    metric_names = ["coverage", "freshness_lite", "readability", "example_pass_rate"]
    for name in metric_names:
        if name in diff:
            checks.append(_regression_check(name, float(diff[name]), metric_warn, metric_crit))

    return Verdict(checks=checks)
