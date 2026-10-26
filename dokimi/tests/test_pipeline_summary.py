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


def test_summary_averages_the_judged_scores_and_skips_a_failed_question(tmp_path, monkeypatch):
    """Each average is over the questions that have that score; a question whose judge
    failed (null conditions, no delta) adds to none of them."""
    result = _run(tmp_path, monkeypatch, [
        _evaluation("q1", 80.5, 50),
        _evaluation("q2", 59.5, 40),
        _evaluation("q3", None, None),
    ])

    assert result["steps"]["evaluation"]["summary"] == {"with_avg": 70.0, "without_avg": 45.0, "delta_avg": 25.0}


def test_summary_is_left_out_when_no_question_was_scored(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch, [_evaluation("q1", None, None)])

    assert "summary" not in result["steps"]["evaluation"]
