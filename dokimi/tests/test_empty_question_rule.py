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
from dokimi.records import RecordError
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
    answered = {"with_docs": {"answer": "a"}}
    with pytest.raises(EmptyQuestionError, match="record #1"):
        require_question_texts([{"question_text": "ok", **answered}, {"question_text": "", **answered}])


def test_helper_skips_a_record_with_nothing_to_judge():
    """An answer-error record has no answer, so no judge sees it: it is not refused."""
    unanswered = [{"question_id": "q2", "question_text": "", "error": "timeout",
                   "with_docs": None, "without_docs": None},
                  {"question_id": "q3", "arms": {"baseline": {"answer": ""}}}]
    assert require_question_texts(unanswered) is None
    # The same records with an answer are refused, so the skip above is the answer check.
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        require_question_texts([{**unanswered[0], "with_docs": {"answer": ANSWER}}])


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
        build_pairs(answers, n_bases=5, min_chars=10)


def test_paths_still_pass_the_question_to_the_judge():
    judge = MagicMock()
    judge.compare.return_value = MagicMock()
    run_arm_pairwise([_arms_record("q1", "How do I fit?")], judge, library="lib")
    assert judge.compare.call_args.args[1] == "How do I fit?"

    scorer = MagicMock(return_value=50.0)
    score_measure([_arms_record("q1", "How do I fit?")], scorer, name="j", library="lib")
    assert {c.args[1] for c in scorer.call_args_list} == {"How do I fit?"}


# ── the CLI: an error line and exit 2, not a traceback ───────────────────

def test_cli_reports_the_record_and_exits_2(tmp_path, monkeypatch, capsys):
    from dokimi import cli

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"baseline_arm": "baseline",
                                   "answers": [_arms_record("q2", "")]}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["dokimi", "eval", "score", "pairwise", "--answers", str(answers)])
    monkeypatch.setattr("dokimi.commands.judge_reliability.pairwise_judge",
                        lambda args: (MagicMock(), "stub/judge"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2  # 1 is the verdict code of judge-validity and the --strict gates
    err = capsys.readouterr().err
    assert err.startswith("Error: ") and "'q2'" in err and "Traceback" not in err


def test_arms_run_refuses_a_blank_question_before_any_answer_is_paid_for(tmp_path, monkeypatch, capsys):
    """The question-set reader accepts a whitespace-only question; the judge would refuse it
    only after every arm had answered, and the run's output is never written."""
    import dokimi.eval.arms_run as arms_run
    from dokimi import cli

    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": [{"id": "q1", "question": "How?"},
                                                   {"id": "q2", "question": "   "}]}), encoding="utf-8")
    answered = []
    monkeypatch.setattr(ArmRunner, "run", lambda self, library_name, questions, **kw: answered.append(1) or [
        _arms_record(q["id"], q["question"]) for q in questions])
    monkeypatch.setattr(arms_run, "probe_sampling", lambda *a: None)
    out = tmp_path / "arms.json"
    monkeypatch.setattr(sys, "argv", ["dokimi", "arms", "run", "--product", "lib", "--questions", str(questions),
                                      "--arms", "baseline", "--judge", "--out-json", str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.value.code == 2 and err.startswith("Error: question 'q2'"), err  # not "Error building arms"
    assert answered == [] and not out.exists()


def test_matrix_run_reports_a_blank_question_as_bad_input_not_a_failed_cell(tmp_path, monkeypatch, capsys):
    """A blank question is the question set's fault, so it exits 2 like ``arms run``, not 1 as a cell failure."""
    import dokimi.eval.arms_run as arms_run
    from dokimi import cli

    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": [{"id": "q1", "question": "How?"},
                                                   {"id": "q2", "question": "   "}]}), encoding="utf-8")
    cells = tmp_path / "cells.json"
    cells.write_text(json.dumps({"matrix": {"cells": [
        {"id": "c", "model": "m", "provider": "openai", "harness": "arms-runner"}]}}), encoding="utf-8")
    answered = []
    monkeypatch.setattr(ArmRunner, "run", lambda self, library_name, questions, **kw: answered.append(1) or [
        _arms_record(q["id"], q["question"]) for q in questions])
    monkeypatch.setattr(arms_run, "probe_sampling", lambda *a: None)
    out = tmp_path / "mx"
    monkeypatch.setattr(sys, "argv", ["dokimi", "arms", "matrix-run", "--product", "lib", "--questions", str(questions),
                                      "--arms", "baseline", "--matrix-cells", str(cells), "--judge",
                                      "--out-dir", str(out)])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    err = capsys.readouterr().err
    assert exc.value.code == 2 and err.startswith("Error: question 'q2'"), err  # not "Error running matrix cell"
    assert answered == [] and not list(out.glob("cells/*.json"))


# ── a record that would never be judged is skipped, not refused ──────────

def _blank(qid="q2", **extra):
    return {"question_id": qid, "question_text": "  ", **extra}


def test_pairwise_skips_a_blank_record_without_a_baseline_or_with_only_excluded_arms():
    judge = MagicMock()
    judge.compare.return_value = MagicMock()
    records = [_arms_record("q1", "How?"),
               _blank("q2", arms={"skill": {"answer": ANSWER}}),
               _blank("q3", arms={"baseline": {"answer": ANSWER}, "other": {"answer": ANSWER}})]
    report = run_arm_pairwise(records, judge, arms=["skill"])
    assert set(report.verdicts["skill"]) == {"q1"}


def test_checklist_skips_a_blank_record_without_points():
    ask = MagicMock(return_value='{"present": true}')
    report = run_checklist([_arms_record("q1", "How?"), _arms_record("q2", "")], {"q1": ["fit"]}, ask)
    assert report.n_without_points == 1 and "q2" not in report.verdicts


def test_score_measure_skips_a_blank_record_with_only_empty_answers():
    scorer = MagicMock(return_value=50.0)
    m = score_measure([_arms_record("q1", "How?"), _blank(arms={"baseline": {"answer": ""}})],
                      scorer, name="j", library="lib")
    assert {qid for qid, _ in m.scores} == {"q1"}


@pytest.mark.parametrize("blank", [_blank("q9", arms={}), _blank("q9", arms={"baseline": {"answer": "short"}})])
def test_degradation_pairs_skip_a_blank_record_that_cannot_become_a_pair(blank):
    answers = [_arms_record(f"q{i}", f"question {i}?") for i in range(4)] + [blank]
    pairs = build_pairs(answers, n_bases=10, min_chars=100)
    assert pairs and "q9" not in {p["base_id"] for p in pairs}


def test_degradation_pairs_refuse_a_record_without_an_id():
    """No id: a malformed record (dokimi.records), refused before the question check."""
    answers = [_arms_record(f"q{i}", f"question {i}?") for i in range(4)]
    noid = {"question_text": " ", "arms": {"baseline": {"answer": ANSWER}}}
    with pytest.raises(RecordError, match=r"answers\[4\]: missing question_id"):
        build_pairs(answers + [noid], n_bases=10, min_chars=10)


def test_evaluate_answers_keeps_an_answer_error_record_with_a_blank_question():
    judge = Judge.__new__(Judge)
    judge.provider, judge.model = "stub", "judge"
    judge._judge_answer = MagicMock(return_value={"aggregate": 70.0})
    answers = [_legacy_record("q1", "How?"),
               {"question_id": "q2", "question_text": "", "error": "timeout", "with_docs": None, "without_docs": None}]
    rows = Judge.evaluate_answers(judge, "lib", answers)
    assert [r["question_id"] for r in rows] == ["q1", "q2"]
    assert judge._judge_answer.call_count == 2  # q1's two answers only


def test_arm_runner_judge_reads_a_question_only_record_once():
    """The guard and the row agree: a ``question``-only record is judged and kept, no KeyError."""
    runner = ArmRunner.__new__(ArmRunner)
    runner.judge_context = MagicMock()
    runner.judge_context.resolve_with_source.return_value = ("", "snippets")
    judge = MagicMock()
    judge.score_answer.return_value = {"aggregate": 60.0}
    rec = {"question_id": "q1", "question": "How?", "arms": {"baseline": {"answer": ANSWER}}}
    [row] = runner.judge(judge, "lib", [rec])
    assert row["question_text"] == "How?"
    assert judge.score_answer.call_args.kwargs["question"] == "How?"


def test_question_text_of_skips_a_blank_spelling():
    """The arm runner answers with this text, so it matches what the judge's check accepts."""
    from dokimi.eval.question_text import question_text_of

    assert question_text_of({"question": "   ", "question_text": "Real?"}) == "Real?"
    assert question_text_of({"question": "  "}) == ""


# ── RAGAS: refused before any RAGAS call, and not swallowed as a warning ─

def test_ragas_refuses_before_any_call(monkeypatch):
    import dokimi.eval.ragas_eval as ragas_mod

    ragas_evaluate = MagicMock()
    monkeypatch.setattr(ragas_mod, "_import_ragas_runtime", lambda: (MagicMock(), ragas_evaluate))
    ev = ragas_mod.RagasEvaluator.__new__(ragas_mod.RagasEvaluator)
    ev._requested = {"faithfulness", "answer_relevancy"}
    ev._metric_objects, ev._ragas_llm = [], MagicMock()
    rec = {"question_id": "q2", "question_text": " ",
           "with_docs": {"answer": "a", "retrieved_docs": [{"snippet": "s"}]}, "without_docs": {"answer": "b"}}
    with pytest.raises(EmptyQuestionError, match="'q2'"):
        ev.evaluate([_legacy_record("q1", "How?"), rec])
    ragas_evaluate.assert_not_called()


def test_ragas_refusal_propagates_through_the_command_wrapper(monkeypatch, capsys, tmp_path):
    import dokimi.eval.ragas_eval as ragas_mod
    from dokimi.commands.evaluate import _run_ragas_eval

    class Refusing:
        def __init__(self, **kw):
            pass

        def evaluate(self, answers, include_without_docs=True):
            raise EmptyQuestionError("question 'q2' has no question text")

    monkeypatch.setattr(ragas_mod, "RagasEvaluator", Refusing)
    with pytest.raises(EmptyQuestionError):
        _run_ragas_eval([], tmp_path / "x.json", object())
    assert "RAGAS evaluation failed" not in capsys.readouterr().out


# ── no billed sampling probe before the refusal ──────────────────────────

@pytest.mark.parametrize("cmd", [
    ["eval", "score", "pairwise"],
    ["eval", "score", "checklist", "--questions", "{questions}"],
    ["eval", "judge", "agreement", "--no-stored", "--judges", "anthropic/claude-haiku-4-5"],
])
def test_no_probe_call_before_the_refusal(cmd, tmp_path, monkeypatch, capsys):
    from dokimi import cli

    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"baseline_arm": "baseline",
                                   "answers": [_arms_record("q1", "How?"), _arms_record("q2", "")]}),
                       encoding="utf-8")
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": [
        {"id": "q1", "question": "How?", "expected_topics": ["fit"]},
        {"id": "q2", "question": "Why?", "expected_topics": ["predict"]}]}), encoding="utf-8")
    calls = []
    monkeypatch.setattr("dokimi.eval.arms_run.probe_sampling", lambda *a: calls.append(a))
    monkeypatch.setattr("dokimi.llm.unsupported_sampling_params", lambda *a, **k: [])
    argv = [a.format(questions=questions) for a in cmd]
    monkeypatch.setattr(sys, "argv", ["dokimi", *argv, "--answers", str(answers), "--judge-temperature", "0.2"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2 and "'q2'" in capsys.readouterr().err
    assert calls == []
