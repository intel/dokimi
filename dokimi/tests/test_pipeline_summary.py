# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The summary ``EvaluationPipeline.run`` returns for the judged questions."""

from __future__ import annotations

import json

import pytest

from dokimi.orchestrator.pipeline import EvaluationPipeline


def _evaluation(qid, with_score, without_score):
    delta = None if with_score is None or without_score is None else with_score - without_score
    return {
        "question_id": qid,
        "question_text": f"Question {qid}?",
        "with_docs": None if with_score is None else {"aggregate": with_score},
        "without_docs": None if without_score is None else {"aggregate": without_score},
        "delta": delta,
    }


def _run(tmp_path, monkeypatch: pytest.MonkeyPatch, evaluations):
    """Run the pipeline on a cached question set with the LLM steps replaced."""
    monkeypatch.setattr(EvaluationPipeline, "_generate_answers", lambda self, *a, **k: [])
    monkeypatch.setattr(EvaluationPipeline, "_evaluate_answers", lambda self, *a, **k: evaluations)
    monkeypatch.setattr(EvaluationPipeline, "_generate_report", lambda self, *a, **k: "report")
    output_dir = tmp_path / "run"
    (output_dir / "personas").mkdir(parents=True)
    (output_dir / "questions").mkdir()
    (output_dir / "personas" / "oneTBB.json").write_text(json.dumps({"personas": []}), encoding="utf-8")
    questions = [{"id": e["question_id"], "question": e["question_text"], "source_type": "manual"}
                 for e in evaluations]
    (output_dir / "questions" / "oneTBB.json").write_text(json.dumps({"questions": questions}), encoding="utf-8")
    pipeline = EvaluationPipeline(product="oneTBB", output_dir=output_dir, description="Threading Building Blocks")
    return pipeline.run(concurrency=1)


def test_summary_averages_the_complete_pairs_and_skips_a_failed_question(tmp_path, monkeypatch):
    """Each average is over the complete pairs. A question whose judge failed (null
    conditions, no delta) adds to none of them, and neither does a question scored in
    one arm only: the context mean must not cover a question its baseline mean lacks."""
    result = _run(tmp_path, monkeypatch, [
        _evaluation("q1", 80.5, 50),
        _evaluation("q2", 59.5, 40),
        _evaluation("q3", None, None),
        _evaluation("q4", 100, None),
        _evaluation("q5", None, 30),
    ])

    assert result["steps"]["evaluation"]["summary"] == {"with_avg": 70.0, "without_avg": 45.0, "delta_avg": 25.0}


def test_summary_rounds_the_delta_from_the_unrounded_means(tmp_path, monkeypatch):
    """One pair, 60.04 against 50.06: the delta of the unrounded means is 9.98, which rounds to
    10.0, while the rounded means 60.0 and 50.1 would give 9.9. A baseline-only question must
    not reach the baseline mean either (the per-arm formula would give 40.0)."""
    result = _run(tmp_path, monkeypatch, [
        _evaluation("q1", 60.04, 50.06),
        _evaluation("q2", None, 30),
    ])

    assert result["steps"]["evaluation"]["summary"] == {"with_avg": 60.0, "without_avg": 50.1, "delta_avg": 10.0}


def test_summary_is_left_out_when_no_question_was_scored(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, [_evaluation("q1", None, None)])

    assert "summary" not in result["steps"]["evaluation"]


def test_summary_says_how_many_questions_it_left_out(tmp_path, monkeypatch, capsys):
    """3 of 10 questions are unjudged retrieval misses: the means are over 7, and the
    run result and the printed line say so instead of reading as a result over all 10."""
    evaluations = [_evaluation(f"q{i}", 80, 60) for i in range(7)]
    evaluations += [_evaluation(f"m{i}", None, 60) for i in range(3)]

    result = _run(tmp_path, monkeypatch, evaluations)

    step = result["steps"]["evaluation"]
    assert (step["paired"], step["unpaired"]) == (7, 3)
    printed = capsys.readouterr().out
    assert "Evaluated 10 answers: context=80.0, baseline=60.0, delta=+20.0" in printed
    assert "over the 7 scored in both arms; 3 left out" in printed


def test_summary_does_not_say_left_out_when_every_question_is_paired(tmp_path, monkeypatch, capsys):
    result = _run(tmp_path, monkeypatch, [_evaluation("q1", 80, 60), _evaluation("q2", 70, 50)])

    step = result["steps"]["evaluation"]
    assert (step["paired"], step["unpaired"]) == (2, 0)
    printed = capsys.readouterr().out
    assert "Evaluated 2 answers: context=75.0, baseline=55.0, delta=+20.0" in printed
    assert "left out" not in printed
