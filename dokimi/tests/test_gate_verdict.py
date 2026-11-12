# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The shared Verdict: what each property answers, and that the answers agree."""

import dataclasses
import typing

from dokimi.gate.regression import detect_regressions
from dokimi.gate.trust_gate import trust_label
from dokimi.gate.verdict import Check, Verdict


def test_a_critical_regression_reads_as_fail_everywhere():
    # the status, the flags and the check's severity give one answer
    verdict = detect_regressions({"score": -0.5}, {})
    assert verdict.has_critical
    assert not verdict.has_warnings
    assert verdict.status == "FAIL"
    assert verdict.check("score").as_dict()["severity"] == "fail"


def test_a_warn_regression_reads_as_warn():
    verdict = detect_regressions({"score": -0.04}, {})
    assert verdict.has_warnings and not verdict.has_critical
    assert verdict.status == "WARN"


def test_a_verdict_that_measured_nothing_does_not_pass():
    assert not Verdict().passed
    assert Verdict(enabled=False).passed


def test_a_verdict_with_a_measured_passing_check_passes():
    assert Verdict(checks=[Check("x", passed=True)]).passed


def test_trust_label_covers_a_disabled_verdict():
    assert trust_label(Verdict(enabled=False)) == "DISABLED"


def test_check_severity_and_level_are_literal_types():
    # pins the annotations a type checker reads; a severity typo then fails mypy, not the status
    hints = typing.get_type_hints(Check)
    assert hints["severity"] == typing.Literal["info", "warn", "fail"]
    assert hints["level"] == typing.Optional[typing.Literal["OK", "WARN", "CRITICAL"]]


def test_verdict_has_no_multirun_std_field():
    assert "multirun_std" not in {f.name for f in dataclasses.fields(Verdict)}

