# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An arms cell whose answer call failed is an error, apart from the scores that count."""

from __future__ import annotations

from unittest.mock import MagicMock

from dokimi.eval.arm_runner import ArmRunner
from dokimi.eval.arm_summary import cell_values


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
