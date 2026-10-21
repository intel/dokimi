# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for QuestionQualityAnalyzer."""
import json
from unittest.mock import patch


from dokimi.questions.quality_analyzer import (
    QuestionQualityAnalyzer,
    QualityReport,
)


# ---------------------------------------------------------------------------
# classify_question
# ---------------------------------------------------------------------------

def _mock_llm(response: str):
    return patch("dokimi.questions.quality_analyzer.llm_call", return_value=response)


def test_classify_question_success():
    payload = json.dumps({"difficulty": "advanced", "trivial": False, "reason": "requires docs"})
    with _mock_llm(payload):
        analyzer = QuestionQualityAnalyzer()
        result = analyzer.classify_question("How does the task_scheduler_observer work?", "oneTBB")
    assert result.difficulty == "advanced"
    assert result.trivial is False
    assert result.error is None


def test_classify_question_trivial():
    payload = json.dumps({"difficulty": "beginner", "trivial": True, "reason": "generic concept"})
    with _mock_llm(payload):
        analyzer = QuestionQualityAnalyzer()
        result = analyzer.classify_question("What is a thread?", "oneTBB")
    assert result.trivial is True


def test_classify_question_strips_markdown_fence():
    payload = "```json\n{\"difficulty\": \"intermediate\", \"trivial\": false, \"reason\": \"ok\"}\n```"
    with _mock_llm(payload):
        analyzer = QuestionQualityAnalyzer()
        result = analyzer.classify_question("How do I use parallel_for?", "oneTBB")
    assert result.difficulty == "intermediate"
    assert result.error is None


def test_classify_question_llm_error_returns_default():
    with patch("dokimi.questions.quality_analyzer.llm_call", side_effect=RuntimeError("boom")):
        analyzer = QuestionQualityAnalyzer()
        result = analyzer.classify_question("Some question?", "oneTBB")
    assert result.difficulty == "intermediate"
    assert result.trivial is False
    assert result.error is not None


def test_classify_question_bad_json_returns_default():
    with _mock_llm("not json at all"):
        analyzer = QuestionQualityAnalyzer()
        result = analyzer.classify_question("Some question?", "oneTBB")
    assert (result.difficulty, result.trivial) == ("intermediate", False)
    assert "No JSON object found" in result.error


# ---------------------------------------------------------------------------
# analyze (full pipeline)
# ---------------------------------------------------------------------------

def _make_classifications(specs):
    """Return sequential llm_call responses."""
    responses = []
    for diff, trivial in specs:
        responses.append(json.dumps({"difficulty": diff, "trivial": trivial, "reason": "test"}))
    return responses


def test_analyze_builds_report():
    questions = [
        "What is oneTBB?",           # beginner, trivial
        "How to use parallel_for?",  # intermediate, non-trivial
        "Explain task_arena internals",  # advanced, non-trivial
        "How to tune performance?",  # advanced, non-trivial
        "Install oneTBB on Linux?",  # beginner, non-trivial
    ]
    specs = [
        ("beginner", True),
        ("intermediate", False),
        ("advanced", False),
        ("advanced", False),
        ("beginner", False),
    ]
    responses = _make_classifications(specs)
    call_iter = iter(responses)
    with patch("dokimi.questions.quality_analyzer.llm_call", side_effect=lambda *a, **kw: next(call_iter)):
        analyzer = QuestionQualityAnalyzer(concurrency=2)
        report = analyzer.analyze(questions, "oneTBB")

    assert report.total == 5
    assert report.trivial_count == 1
    assert report.trivial_pct == 20.0
    assert report.difficulty_distribution["advanced"] == 2
    assert report.difficulty_distribution["intermediate"] == 1
    assert report.difficulty_distribution["beginner"] == 2
    assert 0.0 <= report.diversity_score <= 1.0
    assert len(report.questions) == 5
    assert len(report.recommendations) >= 1


def test_analyze_empty_questions():
    analyzer = QuestionQualityAnalyzer()
    report = analyzer.analyze([], "oneTBB")
    assert report.total == 0
    assert report.trivial_pct == 0.0
    assert report.diversity_score == 0.0


# ---------------------------------------------------------------------------
# _diversity_score
# ---------------------------------------------------------------------------

def test_diversity_score_uniform():
    dist = {"beginner": 10, "intermediate": 10, "advanced": 10}
    score = QuestionQualityAnalyzer._diversity_score(dist, 30)
    assert abs(score - 1.0) < 1e-9


def test_diversity_score_all_same():
    dist = {"beginner": 30, "intermediate": 0, "advanced": 0}
    score = QuestionQualityAnalyzer._diversity_score(dist, 30)
    assert score == 0.0


def test_diversity_score_zero_total():
    assert QuestionQualityAnalyzer._diversity_score({}, 0) == 0.0


# ---------------------------------------------------------------------------
# _build_recommendations
# ---------------------------------------------------------------------------

def test_recommendations_high_trivial():
    recs = QuestionQualityAnalyzer._build_recommendations(
        {"beginner": 10, "intermediate": 10, "advanced": 10}, 30, 25.0
    )
    assert any("trivial" in r for r in recs)


def test_recommendations_beginner_heavy():
    recs = QuestionQualityAnalyzer._build_recommendations(
        {"beginner": 25, "intermediate": 3, "advanced": 2}, 30, 0.0
    )
    assert any("beginner" in r for r in recs)


def test_recommendations_few_advanced():
    recs = QuestionQualityAnalyzer._build_recommendations(
        {"beginner": 10, "intermediate": 18, "advanced": 2}, 30, 0.0
    )
    assert any("advanced" in r for r in recs)


def test_recommendations_balanced():
    recs = QuestionQualityAnalyzer._build_recommendations(
        {"beginner": 10, "intermediate": 10, "advanced": 10}, 30, 5.0
    )
    assert any("well-balanced" in r for r in recs)


# ---------------------------------------------------------------------------
# save_report
# ---------------------------------------------------------------------------

def test_save_report(tmp_path):
    report = QualityReport(
        library_name="oneTBB",
        total=3,
        difficulty_distribution={"beginner": 1, "intermediate": 1, "advanced": 1},
        trivial_count=0,
        trivial_pct=0.0,
        diversity_score=1.0,
        questions=[],
        trivial_questions=[],
        recommendations=["Looks good."],
    )
    out = tmp_path / "report.json"
    analyzer = QuestionQualityAnalyzer()
    analyzer.save_report(report, out)
    data = json.loads(out.read_text())
    assert data["library_name"] == "oneTBB"
    assert data["total"] == 3
    assert data["diversity_score"] == 1.0
