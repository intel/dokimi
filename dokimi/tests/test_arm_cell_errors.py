# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An arms cell whose answer call failed is an error, apart from the scores that count."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import dokimi.eval.arm_runner as arm_runner_mod
from dokimi.eval.arm_runner import ArmRunner
from dokimi.eval.arm_summary import cell_values
from dokimi.treatments import BaselineTreatment


def test_arm_runner_judge_turns_an_errored_answer_into_an_error_cell():
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    runner.judge_context.resolve_with_source.return_value = ("", "snippets")
    judge = MagicMock()
    judge.score_answer.return_value = {"aggregate": 60.0}
    rec = {"question_id": "q1", "question_text": "How?",
           "arms": {"baseline": {"answer": "base"},
                    "skill": {"answer": None, "error": "generation_failed: HTTP 500"}}}
    [row] = runner.judge(judge, "lib", [rec], baseline_arm="baseline")
    assert row["scores"]["skill"] == {"error": "generation_failed: HTTP 500"}
    assert judge.score_answer.call_count == 1


def test_an_errored_cell_is_counted_apart_and_left_out_of_the_mean():
    evaluations = [
        {"question_id": "1", "question_text": "Q1",
         "scores": {"baseline": {"aggregate": 80}, "skill": {"error": "judge: timeout"}}},
        {"question_id": "2", "question_text": "Q2",
         "scores": {"baseline": {"aggregate": 60}, "skill": {"aggregate": 70}}},
    ]
    values, errors = cell_values(evaluations, ["baseline", "skill"])
    assert values == {"baseline": [80.0, 60.0], "skill": [70.0]}
    assert errors == {"baseline": 0, "skill": 1}


@pytest.mark.parametrize("reply", ["", "   \n\t", None])
def test_a_single_turn_empty_reply_is_an_error_cell_not_an_answer(monkeypatch, reply):
    usage = {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4}
    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", lambda *a, **k: (reply, usage))
    runner = ArmRunner([BaselineTreatment()])
    [rec] = runner.run("lib", [{"id": "q1", "question": "How?"}], concurrency=1)
    cell = rec["arms"]["baseline"]
    assert cell["answer"] is None
    assert cell["error"] == "empty_reply"
    # The call was paid for, so its usage is still recorded, as for a failed session.
    assert cell["token_usage"]["total_tokens"] == 4


def test_judge_does_not_score_a_whitespace_reply_and_records_it_as_an_error():
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    runner.judge_context.resolve_with_source.return_value = ("", "snippets")
    judge = MagicMock()
    judge.score_answer.return_value = {"aggregate": 60.0}
    rec = {"question_id": "q1", "question_text": "How?",
           "arms": {"baseline": {"answer": "base"}, "skill": {"answer": "   "}}}
    [row] = runner.judge(judge, "lib", [rec], baseline_arm="baseline")
    assert row["scores"]["skill"] == {"error": "empty_reply"}
    assert judge.score_answer.call_count == 1


def test_judge_records_a_legacy_empty_answer_without_error_as_an_error_cell():
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    runner.judge_context.resolve_with_source.return_value = ("", "snippets")
    judge = MagicMock()
    judge.score_answer.return_value = {"aggregate": 60.0}
    rec = {"question_id": "q1", "question_text": "How?",
           "arms": {"baseline": {"answer": "base"}, "skill": {"answer": ""}}}
    [row] = runner.judge(judge, "lib", [rec], baseline_arm="baseline")
    assert row["scores"]["skill"] == {"error": "empty_reply"}
    assert cell_values([row], ["baseline", "skill"])[1] == {"baseline": 0, "skill": 1}


def test_a_judge_exception_with_an_empty_message_is_still_an_error_cell():
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    runner.judge_context.resolve_with_source.return_value = ("", "snippets")

    def score_answer(**kw):
        if kw["answer"] == "x":
            raise TimeoutError()  # str() of this is ""
        return {"aggregate": 60.0}

    judge = MagicMock()
    judge.score_answer.side_effect = score_answer
    rec = {"question_id": "q1", "question_text": "How?",
           "arms": {"baseline": {"answer": "base"}, "skill": {"answer": "x"}}}
    [row] = runner.judge(judge, "lib", [rec], baseline_arm="baseline")
    assert row["scores"]["skill"]["error"] == "TimeoutError"
    assert cell_values([row], ["baseline", "skill"])[1] == {"baseline": 0, "skill": 1}
