# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for critical bands enforcement."""

import pytest
from dokimi.gate.critical_bands import BandViolation, check_critical_bands


def test_critical_bands_pass():
    """All conditions met → no violations."""
    spec = {
        "critical_bands": {
            "fail_on": [
                {"condition": "score_below", "value": 0.60},
                {"condition": "coverage_below", "value": 0.70},
            ]
        }
    }
    summary = {"score": 0.75, "coverage": 0.80, "freshness_lite": 1.0, "readability": 1.0}
    
    result = check_critical_bands(summary, spec)
    
    assert result.enabled is True
    assert result.passed is True
    assert not result.has_violations


def test_critical_bands_violation():
    """Condition violated → has_violations."""
    spec = {
        "critical_bands": {
            "fail_on": [
                {"condition": "score_below", "value": 0.60},
                {"condition": "coverage_below", "value": 0.70},
            ]
        }
    }
    summary = {"score": 0.55, "coverage": 0.65, "freshness_lite": 1.0, "readability": 1.0}
    
    result = check_critical_bands(summary, spec)
    
    assert result.has_violations
    assert not result.passed
    assert len([v for v in result.violations if v.violated]) == 2


def test_critical_bands_exactly_at_threshold():
    """Value exactly at threshold → NOT violated."""
    spec = {
        "critical_bands": {
            "fail_on": [{"condition": "score_below", "value": 0.60}]
        }
    }
    summary = {"score": 0.60, "coverage": 1.0, "freshness_lite": 1.0, "readability": 1.0}
    
    result = check_critical_bands(summary, spec)
    
    assert result.passed


def test_critical_bands_disabled():
    """No fail_on list → disabled."""
    spec = {"critical_bands": {}}
    summary = {"score": 0.10, "coverage": 0.10, "freshness_lite": 0.0, "readability": 0.0}
    
    result = check_critical_bands(summary, spec)
    
    assert result.enabled is False
    assert result.passed


def test_critical_bands_unknown_condition():
    """Unknown condition → ValueError."""
    spec = {
        "critical_bands": {
            "fail_on": [{"condition": "typo_below", "value": 0.5}]
        }
    }
    summary = {"score": 0.70}
    
    with pytest.raises(ValueError, match="Unknown critical_bands condition"):
        check_critical_bands(summary, spec)


def test_critical_bands_missing_condition_key():
    """Missing 'condition' key → ValueError."""
    spec = {
        "critical_bands": {
            "fail_on": [{"value": 0.5}]
        }
    }
    summary = {"score": 0.70}
    
    with pytest.raises(ValueError, match="missing 'condition' key"):
        check_critical_bands(summary, spec)


def test_critical_bands_non_dict_entry():
    """Non-dict entry → ValueError."""
    spec = {
        "critical_bands": {
            "fail_on": ["score_below"]
        }
    }
    summary = {"score": 0.70}
    
    with pytest.raises(ValueError, match="must be mappings"):
        check_critical_bands(summary, spec)


def test_critical_bands_all_conditions():
    """Test all 4 supported conditions."""
    spec = {
        "critical_bands": {
            "fail_on": [
                {"condition": "score_below", "value": 0.80},
                {"condition": "coverage_below", "value": 0.75},
                {"condition": "freshness_below", "value": 0.70},
                {"condition": "readability_below", "value": 0.65},
            ]
        }
    }
    summary = {"score": 0.60, "coverage": 0.60, "freshness_lite": 0.60, "readability": 0.60}
    
    result = check_critical_bands(summary, spec)
    
    assert len(result.violations) == 4
    assert all(v.violated for v in result.violations)


# condition -> the summary key it reads
_CONDITION_KEYS = [
    ("score_below", "score"),
    ("coverage_below", "coverage"),
    ("freshness_below", "freshness_lite"),
    ("readability_below", "readability"),
]


@pytest.mark.parametrize(("condition", "key"), _CONDITION_KEYS)
def test_each_condition_reads_its_own_summary_key(condition, key):
    """Only the named key is in the summary: a condition that read another key
    would see the 0.0 default and report a violation."""
    spec = {"critical_bands": {"fail_on": [{"condition": condition, "value": 0.5}]}}

    result = check_critical_bands({key: 0.9}, spec)

    assert result.violations == [BandViolation(condition, 0.5, 0.9, False)]
    assert result.passed


@pytest.mark.parametrize(("condition", "key"), _CONDITION_KEYS)
def test_each_condition_is_strictly_below(condition, key):
    """"below" is strict for every condition, not just score_below."""
    spec = {"critical_bands": {"fail_on": [{"condition": condition, "value": 0.7}]}}

    at = check_critical_bands({key: 0.7}, spec)
    under = check_critical_bands({key: 0.69}, spec)

    assert at.violations == [BandViolation(condition, 0.7, 0.7, False)]
    assert under.violations == [BandViolation(condition, 0.7, 0.69, True)]


@pytest.mark.parametrize(("condition", "key"), _CONDITION_KEYS)
def test_a_missing_metric_reads_as_zero_and_violates(condition, key):
    spec = {"critical_bands": {"fail_on": [{"condition": condition, "value": 0.1}]}}

    result = check_critical_bands({}, spec)

    assert result.violations == [BandViolation(condition, 0.1, 0.0, True)]
    assert not result.passed


def test_a_band_without_a_value_has_a_zero_floor():
    """No ``value`` means a 0.0 floor, which no score in [0, 1] is below."""
    spec = {"critical_bands": {"fail_on": [{"condition": "score_below"}]}}

    result = check_critical_bands({"score": 0.0}, spec)

    assert result.violations == [BandViolation("score_below", 0.0, 0.0, False)]


@pytest.mark.parametrize(
    "spec",
    [{}, {"critical_bands": {}}, {"critical_bands": {"fail_on": []}}],
    ids=["no-key", "no-fail_on", "empty-fail_on"],
)
def test_no_bands_means_the_gate_is_disabled(spec):
    """A spec with no band, or an empty list, is disabled and reports nothing."""
    result = check_critical_bands({"score": 0.0}, spec)

    assert (result.enabled, result.violations, result.passed) == (False, [], True)
