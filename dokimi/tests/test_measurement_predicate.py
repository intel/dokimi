# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a measured number is, at every site that reads one: a NaN, an infinity, a bool or an int past
the float range is no measurement; a finite int or float is. Through the public callers: the cell
grader, the score, the process counts, the manifest's validation and the reference checks."""

from __future__ import annotations

import json
import math

import pytest

from dokimi.eval.reference_checks import validate_grading
from dokimi.repos.container import ContainerResult
from dokimi.repos.grading import SCORE_BOUND, gate_ok, outcome, score_value
from dokimi.repos.process_counts import process_values
from dokimi.repos.workspace import Task

HUGE = 10**400  # an int json.loads reads fine; float() of it raises OverflowError
UNMEASURABLE = [math.nan, math.inf, -math.inf, HUGE, -HUGE, True, False]


def _graded(task: Task, run_dir, grade: dict) -> dict:
    (run_dir / "grade.json").write_text(json.dumps(grade), encoding="utf-8")
    return outcome(task, run_dir, ContainerResult(returncode=0, timed_out=False, wall_s=1.0))


def _grade(metrics: dict, pass_: bool | None = True) -> dict:
    return {"schema": "repo_grade.v1", "pass": pass_, "metrics": metrics, "errors": []}


GATE = {"gate": [{"metric": "m", "min": 0.5}]}
SCORED = {"score": {"metric": "m", "max": 1}}


# --- the gate: a metric must be a finite number at or above its minimum -------------------------

@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_a_gate_metric_that_is_not_a_finite_number_fails_the_gate(tmp_path, value):
    task = Task("rev", GATE, tmp_path)
    assert gate_ok(task, _grade({"m": value})) is False


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True])
def test_a_cell_whose_gate_metric_is_not_finite_is_not_a_success(tmp_path, value):
    # the cell path: a score-free gate task, its grade.json read and graded
    task = Task("rev", GATE, tmp_path)
    got = _graded(task, tmp_path, _grade({"m": value}))
    assert got["success"] is False


def test_a_cell_whose_gate_metric_is_finite_and_met_is_a_success(tmp_path):
    task = Task("rev", GATE, tmp_path)
    assert _graded(task, tmp_path, _grade({"m": 0.7}))["success"] is True
    assert _graded(task, tmp_path, _grade({"m": 0.4}))["success"] is False


def test_a_gate_metric_past_the_float_range_compares_exactly(tmp_path):
    # a finite int: the gate sums nothing, so SCORE_BOUND (a report limit) does not apply to it
    task = Task("rev", GATE, tmp_path)
    assert gate_ok(task, _grade({"m": HUGE})) is True
    assert gate_ok(task, _grade({"m": -HUGE})) is False


@pytest.mark.parametrize("minimum", [math.nan, math.inf])
def test_a_gate_with_a_non_finite_minimum_fails_every_metric(tmp_path, minimum):
    task = Task("rev", {"gate": [{"metric": "m", "min": minimum}]}, tmp_path)
    assert gate_ok(task, _grade({"m": 5})) is False


# --- the score: a finite number within SCORE_BOUND, or no measurement ---------------------------

@pytest.mark.parametrize("value", UNMEASURABLE + [SCORE_BOUND * 10])
def test_a_score_that_is_not_a_measurement_is_none(tmp_path, value):
    task = Task("rev", SCORED, tmp_path)
    assert score_value(task, _grade({"m": value}, pass_=None)) is None


def test_a_score_that_is_not_a_measurement_is_a_grader_failure_through_outcome(tmp_path):
    task = Task("rev", SCORED, tmp_path)
    got = _graded(task, tmp_path, _grade({"m": math.nan}, pass_=None))
    assert got["status"] == "grader_failure" and got["score"] is None


def test_a_finite_score_is_kept_as_a_float(tmp_path):
    task = Task("rev", SCORED, tmp_path)
    assert score_value(task, _grade({"m": 3}, pass_=None)) == 3.0
    assert score_value(task, _grade({"m": -0.25}, pass_=None)) == -0.25


# --- the process counts: a count that is not a measurement is left out of the means ------------

@pytest.mark.parametrize("value", UNMEASURABLE + [SCORE_BOUND * 10, "3", None])
def test_a_process_count_that_is_not_a_measurement_is_left_out(value):
    assert process_values({"process": {"tool_calls": value, "commands": 2}}) == {"process:commands": 2.0}


def test_a_finite_process_count_is_kept():
    got = process_values({"process": {"tool_calls": 7, "counters": {"edits": 1.5, "huge": HUGE}}})
    assert got == {"process:tool_calls": 7.0, "counter:edits": 1.5}


# --- the reference checks: a weight or threshold past the float range is a problem, not a crash --

def _grading(**extra) -> dict:
    return {"threshold": 0.5, "checks": [{"type": "match", "value": "x", **extra}]}


@pytest.mark.parametrize("weight", [math.nan, math.inf, HUGE])
def test_a_reference_check_weight_that_is_not_a_finite_float_is_a_problem(weight):
    assert any("weight is not a number" in p for p in validate_grading(_grading(weight=weight)))


def test_a_reference_threshold_past_the_float_range_is_a_problem():
    assert validate_grading({"threshold": HUGE, "checks": [{"type": "match", "value": "x"}]}) == [
        "threshold is not a number"]


def test_a_reference_weight_within_the_float_range_is_valid():
    assert validate_grading(_grading(weight=1e200)) == []
    assert validate_grading(_grading(weight=3)) == []
