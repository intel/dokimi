# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Every judge path refuses a record with no question text before any judge call."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

from dokimi.eval.arm_runner import ArmRunner
from dokimi.eval.checklist import run_checklist
from dokimi.eval.cross_judge import score_measure
from dokimi.eval.degrade import build_pairs
from dokimi.eval.judge import Judge
from dokimi.eval.pairwise import run_arm_pairwise
from dokimi.eval.panel import JudgePanel
from dokimi.eval.question_text import (
    EmptyQuestionError,
    require_question_text,
    require_question_texts,
)

ANSWER = "Call fit() on the estimator, then predict(). " * 10


def _arms_record(qid: str, text):
    rec = {"question_id": qid, "arms": {"baseline": {"answer": ANSWER}, "skill": {"answer": ANSWER + "x"}}}
    if text is not None:
        rec["question_text"] = text
    return rec


def _legacy_record(qid: str, text):
    rec = {"question_id": qid, "with_docs": {"answer": ANSWER}, "without_docs": {"answer": ANSWER}}
    if text is not None:
        rec["question_text"] = text
    return rec


BAD = [None, "", "   "]


# ── the helper ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", BAD)
def test_helper_refuses_missing_or_blank_and_names_the_record(text):
    with pytest.raises(EmptyQuestionError, match="'q7'"):
        require_question_text(_arms_record("q7", text))


def test_helper_names_the_index_when_the_record_has_no_id():
    with pytest.raises(EmptyQuestionError, match="record #1"):
        require_question_texts([{"question_text": "ok"}, {"question_text": ""}])


def test_helper_accepts_the_question_set_spellings():
    assert require_question_text({"question_text": "a"}) == "a"
    assert require_question_text({"question": "b"}) == "b"
    assert require_question_text({"text": "c"}) == "c"
    assert require_question_text({"question_text": "", "question": "d"}) == "d"


def test_error_is_a_value_error():
    assert issubclass(EmptyQuestionError, ValueError)


# ── each judge path ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", BAD)
def test_evaluate_answers_refuses_before_judging(text):
    judge = MagicMock()
    answers = [_legacy_record("q1", "fine"), _legacy_record("q2", text)]
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        Judge.evaluate_answers(judge, "lib", answers)
    judge._evaluate_answer_pair.assert_not_called()


@pytest.mark.parametrize("text", BAD)
def test_panel_evaluate_answers_refuses_before_judging(text):
    panel = MagicMock()
    answers = [_legacy_record("q1", "fine"), _legacy_record("q2", text)]
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        JudgePanel.evaluate_answers(panel, answers, library_name="lib")
    panel._score_answer_entry.assert_not_called()
    panel.evaluate.assert_not_called()


@pytest.mark.parametrize("text", BAD)
def test_arm_runner_judge_refuses_before_judging(text):
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    judge = MagicMock()
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        runner.judge(judge, "lib", [_arms_record("q1", "fine"), _arms_record("q2", text)])
    judge.score_answer.assert_not_called()


@pytest.mark.parametrize("text", BAD)
def test_pairwise_refuses_before_judging(text):
    judge = MagicMock()
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        run_arm_pairwise([_arms_record("q1", "fine"), _arms_record("q2", text)], judge)
    judge.compare.assert_not_called()


@pytest.mark.parametrize("text", BAD)
def test_checklist_refuses_before_judging(text):
    ask = MagicMock()
    records = [_arms_record("q1", "fine"), _arms_record("q2", text)]
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        run_checklist(records, {"q1": ["fit"], "q2": ["predict"]}, ask)
    ask.assert_not_called()


@pytest.mark.parametrize("text", BAD)
def test_score_measure_refuses_before_judging(text):
    scorer = MagicMock(return_value=50.0)
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        score_measure([_arms_record("q1", "fine"), _arms_record("q2", text)], scorer, name="j", library="lib")
    scorer.assert_not_called()


@pytest.mark.parametrize("text", BAD)
def test_degradation_pairs_refuse_instead_of_skipping(text):
    answers = [_arms_record(f"q{i}", f"question {i}?") for i in range(4)] + [_arms_record("q9", text)]
    with pytest.raises(EmptyQuestionError, match="'q9'"):
        build_pairs(answers, n_bases=5)


def test_paths_still_pass_the_question_to_the_judge():
    judge = MagicMock()
    judge.compare.return_value = MagicMock()
    run_arm_pairwise([_arms_record("q1", "How do I fit?")], judge, library="lib")
    assert judge.compare.call_args.args[1] == "How do I fit?"

    scorer = MagicMock(return_value=50.0)
    score_measure([_arms_record("q1", "How do I fit?")], scorer, name="j", library="lib")
    assert {c.args[1] for c in scorer.call_args_list} == {"How do I fit?"}


# ── the CLI: an error line and exit 1, not a traceback ───────────────────

def test_cli_reports_the_record_and_exits_1(tmp_path, monkeypatch, capsys):
    from dokimi import cli

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"baseline_arm": "baseline",
                                   "answers": [_arms_record("q2", "")]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["dokimi", "eval", "pairwise", "--answers", str(answers)])
    monkeypatch.setattr("dokimi.commands.judge_reliability.pairwise_judge",
                        lambda args: (MagicMock(), "stub/judge"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and "'q2'" in err and "Traceback" not in err
