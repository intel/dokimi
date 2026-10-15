# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the soft and hard score gates."""

import pytest

from dokimi.gate.score_gate import check_gate

KINDS = ("soft", "hard")


def _spec(kind, enabled=True, min_score=0.80):
    return {"future": {f"{kind}_gate": {"enabled": enabled, "min_score": min_score}}}


@pytest.mark.parametrize("kind", KINDS)
def test_score_above_threshold_passes(kind):
    result = check_gate({"score": 0.85}, _spec(kind), kind)

    assert result.enabled is True
    assert result.passed is True
    assert result.status == "PASS"
    assert result.min_score == 0.80
    assert result.actual_score == 0.85


@pytest.mark.parametrize("kind", KINDS)
def test_score_below_threshold_fails(kind):
    result = check_gate({"score": 0.65}, _spec(kind), kind)

    assert result.enabled is True
    assert result.passed is False
    assert result.status == "FAIL"


@pytest.mark.parametrize("kind", KINDS)
def test_score_exactly_at_threshold_passes(kind):
    assert check_gate({"score": 0.80}, _spec(kind), kind).passed is True


@pytest.mark.parametrize("kind", KINDS)
def test_disabled_gate_always_passes(kind):
    result = check_gate({"score": 0.10}, _spec(kind, enabled=False, min_score=0.95), kind)

    assert result.enabled is False
    assert result.passed is True
    assert result.status == "DISABLED"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("spec", [{}, {"future": {}}])
def test_missing_config_is_disabled(kind, spec):
    result = check_gate({"score": 0.50}, spec, kind)

    assert result.enabled is False
    assert result.passed is True


def test_each_gate_reads_only_its_own_key():
    spec = {"future": {"soft_gate": {"enabled": True, "min_score": 0.9},
                       "hard_gate": {"enabled": True, "min_score": 0.5}}}

    assert check_gate({"score": 0.7}, spec, "soft").passed is False
    assert check_gate({"score": 0.7}, spec, "hard").passed is True
