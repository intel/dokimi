# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run-wide CLI error (usage limit, no login) stops the answering loops, as the docs promise."""

import pytest

import dokimi.eval.arm_runner as arm_runner_mod
from dokimi.eval.answerer import Answerer
from dokimi.eval.arm_runner import ArmRunner
from dokimi.llm_cli import CliCallError
from dokimi.treatments import BaselineTreatment

USAGE = "codex: You've hit your usage limit."
USAGE_USAGE = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}


def _questions(n):
    return [{"id": f"q{i}", "question": f"Question {i}?"} for i in range(n)]


def _run_wide_on_first_call(calls):
    def fake(*args, **kwargs):
        calls.append(kwargs.get("prompt"))
        raise CliCallError(USAGE, retryable=False, run_wide=True)
    return fake


def test_answerer_asks_no_later_question_after_a_run_wide_error(monkeypatch):
    import dokimi.eval.answerer as answerer_mod
    calls = []
    monkeypatch.setattr(answerer_mod, "llm_call_with_usage", _run_wide_on_first_call(calls))
    answers = Answerer().generate_answers("lib", "lib/id", _questions(4), concurrency=1)
    assert len(calls) == 1
    assert [a["question_id"] for a in answers] == ["q0"]
    assert answers[0]["error"] and USAGE in answers[0]["error"]


def test_answerer_keeps_the_answers_it_already_has_before_a_run_wide_error(monkeypatch):
    import dokimi.eval.answerer as answerer_mod
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs.get("prompt"))
        if len(calls) == 2:
            raise CliCallError(USAGE, retryable=False, run_wide=True)
        return ("a fine answer", USAGE_USAGE)

    monkeypatch.setattr(answerer_mod, "llm_call_with_usage", fake)
    answers = Answerer().generate_answers("lib", "lib/id", _questions(4), concurrency=1)
    assert len(calls) == 2
    assert [a["question_id"] for a in answers] == ["q0", "q1"]
    assert answers[0].get("error") is None


def test_answerer_goes_on_after_an_error_that_is_not_run_wide(monkeypatch):
    import dokimi.eval.answerer as answerer_mod
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs.get("prompt"))
        raise CliCallError("codex: one question's reply", retryable=False, run_wide=False)

    monkeypatch.setattr(answerer_mod, "llm_call_with_usage", fake)
    answers = Answerer().generate_answers("lib", "lib/id", _questions(3), concurrency=1)
    assert len(calls) == 3
    assert len(answers) == 3


def test_arm_runner_asks_no_later_question_after_a_run_wide_error(monkeypatch):
    calls = []
    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", _run_wide_on_first_call(calls))
    records = ArmRunner([BaselineTreatment()]).run("lib", _questions(4), concurrency=1)
    assert len(calls) == 1
    assert [r["question_id"] for r in records] == ["q0"]  # the stopped question keeps a cell per arm
    assert records[0]["arms"]["baseline"]["error"].startswith("generation_failed")


def test_arm_runner_keeps_the_records_it_has_before_a_run_wide_error(monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs.get("prompt"))
        if len(calls) == 2:
            raise CliCallError(USAGE, retryable=False, run_wide=True)
        return ("a fine answer", USAGE_USAGE)

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake)
    records = ArmRunner([BaselineTreatment()]).run("lib", _questions(4), concurrency=1)
    assert len(calls) == 2
    assert [r["question_id"] for r in records] == ["q0", "q1"]  # the stopped question is recorded too


@pytest.mark.parametrize("retryable", [True, False])
def test_arm_runner_goes_on_after_an_error_that_is_not_run_wide(monkeypatch, retryable):
    calls = []

    def fake(*args, **kwargs):
        calls.append(kwargs.get("prompt"))
        raise CliCallError("one question", retryable=retryable, run_wide=False)

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake)
    records = ArmRunner([BaselineTreatment()]).run("lib", _questions(3), concurrency=1)
    assert len(calls) == 3
    assert len(records) == 3
