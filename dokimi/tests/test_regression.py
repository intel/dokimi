# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for regression detection."""

import pytest

from dokimi.gate.regression import RegressionResult, detect_regressions


def test_regression_ok():
    """No regression → all OK."""
    spec = {
        "thresholds": {
            "regressions": {
                "score_drop_warn": 0.03,
                "score_drop_critical": 0.08,
                "metric_drop_warn": 0.05,
                "metric_drop_critical": 0.12,
            }
        }
    }
    diff = {"score": 0.01, "coverage": 0.02, "freshness_lite": 0.00, "readability": -0.01}
    
    result = detect_regressions(diff, spec)
    
    assert result.score_regression.severity == "OK"
    assert not result.has_warnings
    assert not result.has_critical


def test_regression_warn():
    """Drop exceeds warn threshold → WARN."""
    spec = {
        "thresholds": {
            "regressions": {
                "score_drop_warn": 0.03,
                "score_drop_critical": 0.08,
                "metric_drop_warn": 0.05,
                "metric_drop_critical": 0.12,
            }
        }
    }
    diff = {"score": -0.05, "coverage": -0.06, "freshness_lite": 0.00, "readability": 0.00}
    
    result = detect_regressions(diff, spec)
    
    assert result.score_regression.severity == "WARN"
    assert result.metric_regressions[0].severity == "WARN"  # coverage
    assert result.has_warnings
    assert not result.has_critical


def test_regression_critical():
    """Drop exceeds critical threshold → CRITICAL."""
    spec = {
        "thresholds": {
            "regressions": {
                "score_drop_warn": 0.03,
                "score_drop_critical": 0.08,
                "metric_drop_warn": 0.05,
                "metric_drop_critical": 0.12,
            }
        }
    }
    diff = {"score": -0.10, "coverage": -0.15, "freshness_lite": 0.00, "readability": 0.00}
    
    result = detect_regressions(diff, spec)
    
    assert result.score_regression.severity == "CRITICAL"
    assert result.metric_regressions[0].severity == "CRITICAL"
    assert result.has_critical


def test_regression_exactly_at_threshold():
    """Drop exactly at threshold → WARN."""
    spec = {
        "thresholds": {
            "regressions": {
                "score_drop_warn": 0.03,
                "score_drop_critical": 0.08,
                "metric_drop_warn": 0.05,
                "metric_drop_critical": 0.12,
            }
        }
    }
    diff = {"score": -0.03, "coverage": 0.00, "freshness_lite": 0.00, "readability": 0.00}
    
    result = detect_regressions(diff, spec)
    
    assert result.score_regression.severity == "WARN"


def test_regression_improvement():
    """Positive delta → OK."""
    spec = {
        "thresholds": {
            "regressions": {
                "score_drop_warn": 0.03,
                "score_drop_critical": 0.08,
                "metric_drop_warn": 0.05,
                "metric_drop_critical": 0.12,
            }
        }
    }
    diff = {"score": 0.10, "coverage": 0.05, "freshness_lite": 0.00, "readability": 0.00}
    
    result = detect_regressions(diff, spec)
    
    assert result.score_regression.severity == "OK"
    assert not result.has_warnings


def test_regression_missing_thresholds():
    """Missing thresholds → use defaults (0.03 warn, 0.08 critical)."""
    spec = {}
    # -0.01 drop < 0.03 default warn threshold → OK
    diff = {"score": -0.01, "coverage": 0.00, "freshness_lite": 0.00, "readability": 0.00}
    
    result = detect_regressions(diff, spec)
    
    assert result.score_regression.severity == "OK"  # -0.01 < 0.03 warn threshold


# The cases above all pass thresholds equal to the defaults, so they cannot tell
# a spec that is read from one that is ignored. These pin each half separately.

_CUSTOM = {
    "thresholds": {
        "regressions": {
            "score_drop_warn": 0.10,
            "score_drop_critical": 0.20,
            "metric_drop_warn": 0.01,
            "metric_drop_critical": 0.02,
        }
    }
}


def test_spec_thresholds_replace_the_defaults():
    """A -0.05 score drop is WARN by default but OK under a 0.10 warn threshold,
    and a -0.015 coverage drop is OK by default but WARN under a 0.01 one."""
    result = detect_regressions({"score": -0.05, "coverage": -0.015}, _CUSTOM)

    assert result.score_regression == RegressionResult(
        metric="score", delta=-0.05, severity="OK",
        threshold_warn=0.10, threshold_critical=0.20,
    )
    assert result.metric_regressions == [
        RegressionResult(
            metric="coverage", delta=-0.015, severity="WARN",
            threshold_warn=0.01, threshold_critical=0.02,
        )
    ]


def test_spec_critical_thresholds_replace_the_defaults():
    """-0.05 is only WARN under the 0.08 default; a 0.04 critical makes it CRITICAL."""
    spec = {"thresholds": {"regressions": {"score_drop_critical": 0.04,
                                           "metric_drop_critical": 0.04}}}
    result = detect_regressions({"score": -0.05, "coverage": -0.05}, spec)

    assert result.score_regression.severity == "CRITICAL"
    assert result.metric_regressions[0].severity == "CRITICAL"


@pytest.mark.parametrize(
    ("key", "delta", "severity"),
    [
        # The documented defaults, each hit exactly: a drop *at* a threshold counts.
        ("score", -0.03, "WARN"),
        ("score", -0.08, "CRITICAL"),
        ("coverage", -0.05, "WARN"),
        ("coverage", -0.12, "CRITICAL"),
        # Just under each default: a default lowered by 0.01 would classify these higher.
        ("score", -0.029, "OK"),
        ("score", -0.079, "WARN"),
        ("coverage", -0.049, "OK"),
        ("coverage", -0.119, "WARN"),
    ],
)
def test_default_thresholds_apply_at_their_exact_values(key, delta, severity):
    result = detect_regressions({key: delta}, {})

    found = result.score_regression if key == "score" else result.metric_regressions[0]
    assert (found.metric, found.severity) == (key, severity)


def test_a_score_drop_alone_sets_the_summary_flags():
    critical = detect_regressions({"score": -0.5}, {})
    assert critical.has_critical
    assert not critical.has_warnings  # a CRITICAL is not a WARN

    warn = detect_regressions({"score": -0.04}, {})
    assert warn.has_warnings
    assert not warn.has_critical


@pytest.mark.parametrize("name", ["coverage", "freshness_lite", "readability", "example_pass_rate"])
def test_every_tracked_metric_is_classified(name):
    result = detect_regressions({"score": 0.0, name: -0.5}, {})

    assert [(r.metric, r.delta, r.severity) for r in result.metric_regressions] == [
        (name, -0.5, "CRITICAL")
    ]
    assert result.has_critical


def test_an_untracked_metric_is_ignored():
    result = detect_regressions({"score": 0.0, "latency": -0.5}, {})

    assert result.metric_regressions == []
    assert not result.has_warnings


def test_a_diff_without_a_score_reads_as_no_change():
    result = detect_regressions({"coverage": 0.01}, {})

    assert (result.score_regression.delta, result.score_regression.severity) == (0.0, "OK")


def test_no_change_is_ok_even_with_zero_tolerance():
    """With every threshold at 0, any drop is CRITICAL, but no change is not a drop."""
    zero = {"thresholds": {"regressions": dict.fromkeys(
        ("score_drop_warn", "score_drop_critical", "metric_drop_warn", "metric_drop_critical"), 0.0)}}

    unchanged = detect_regressions({"score": 0.0, "coverage": 0.0}, zero)
    dropped = detect_regressions({"score": -0.001, "coverage": 0.0}, zero)

    assert not unchanged.has_warnings and not unchanged.has_critical
    assert dropped.score_regression.severity == "CRITICAL"
