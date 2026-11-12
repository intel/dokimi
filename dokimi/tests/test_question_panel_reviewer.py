# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for QuestionPanelReviewer."""
import json
from unittest.mock import patch


from dokimi.questions.panel_reviewer import (
    DEFAULT_REVIEWERS,
    QuestionPanelReviewer,
    ReviewerVote,
    _build_report,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resp(reviewer, **kwargs):
    dims = {
        "domain_expert":  {"technical_accuracy": 80, "relevance": 80, "depth": 70},
        "user_advocate":  {"realism": 75, "clarity": 85, "usefulness": 80},
        "qa_engineer":    {"evaluability": 90, "answerability": 85, "specificity": 80},
    }[reviewer]
    dims.update(kwargs)
    return json.dumps({"reasoning": "ok", "flags": [], **dims})


def _patch_llm(responses):
    """Deterministic mock: returns the matching response based on reviewer role in prompt."""
    role_map = {r: _resp(r) for r in ["domain_expert", "user_advocate", "qa_engineer"]}
    # Build ordered list for callers that don't embed role in prompt
    it_fallback = iter(responses)
    def _side_effect(prompt, **kw):
        for role in role_map:
            if role in prompt:
                return role_map[role]
        return next(it_fallback)
    return patch("dokimi.questions.panel_reviewer.llm_call", side_effect=_side_effect)


# ── _call_reviewer ────────────────────────────────────────────────────────────

def test_call_reviewer_success():
    pr = QuestionPanelReviewer()
    with _patch_llm([_resp("domain_expert")]):
        vote = pr._call_reviewer("domain_expert", "How to use parallel_for?", "oneTBB")
    assert vote.primary_score is not None
    assert 0 <= vote.primary_score <= 100
    assert vote.error is None


def test_call_reviewer_clamps_scores():
    pr = QuestionPanelReviewer()
    resp = json.dumps({"reasoning":"ok","flags":[],"technical_accuracy":150,"relevance":-5,"depth":80})
    with _patch_llm([resp]):
        vote = pr._call_reviewer("domain_expert", "Q?", "oneTBB")
    assert vote.scores["technical_accuracy"] == 100.0
    assert vote.scores["relevance"] == 0.0


def test_call_reviewer_extracts_flags():
    pr = QuestionPanelReviewer()
    resp = json.dumps({"reasoning":"ok","flags":["too_generic","ambiguous"],
                       "technical_accuracy":50,"relevance":30,"depth":40})
    with _patch_llm([resp]):
        vote = pr._call_reviewer("domain_expert", "Q?", "oneTBB")
    assert "too_generic" in vote.flags
    assert "ambiguous" in vote.flags


def test_call_reviewer_error_returns_vote_with_error():
    pr = QuestionPanelReviewer()
    with patch("dokimi.questions.panel_reviewer.llm_call",
               side_effect=RuntimeError("boom")):
        vote = pr._call_reviewer("domain_expert", "Q?", "oneTBB")
    assert vote.error is not None
    assert vote.primary_score is None


# ── _aggregate ────────────────────────────────────────────────────────────────

def test_aggregate_recommendation_keep():
    pr = QuestionPanelReviewer()
    votes = [
        ReviewerVote("domain_expert", {}, "r", [], primary_score=80.0),
        ReviewerVote("user_advocate", {}, "r", [], primary_score=75.0),
        ReviewerVote("qa_engineer", {}, "r", [], primary_score=85.0),
    ]
    review = pr._aggregate("Q?", votes)
    assert review.recommendation == "keep"
    assert review.panel_score is not None
    assert not review.all_flags


def test_aggregate_recommendation_drop():
    pr = QuestionPanelReviewer()
    votes = [
        ReviewerVote("domain_expert", {}, "r", ["too_generic", "trivially_googleable"], primary_score=25.0),
        ReviewerVote("user_advocate", {}, "r", ["unrealistic"], primary_score=20.0),
        ReviewerVote("qa_engineer", {}, "r", ["opinion_based"], primary_score=15.0),
    ]
    review = pr._aggregate("Q?", votes)
    assert review.recommendation == "drop"
    assert review.needs_attention


def test_aggregate_recommendation_revise():
    pr = QuestionPanelReviewer()
    votes = [
        ReviewerVote("domain_expert", {}, "r", ["too_generic"], primary_score=55.0),
        ReviewerVote("user_advocate", {}, "r", [], primary_score=65.0),
        ReviewerVote("qa_engineer", {}, "r", [], primary_score=60.0),
    ]
    review = pr._aggregate("Q?", votes)
    assert review.recommendation == "revise"


def test_aggregate_collects_all_flags():
    pr = QuestionPanelReviewer()
    votes = [
        ReviewerVote("domain_expert", {}, "r", ["too_generic"], primary_score=60.0),
        ReviewerVote("user_advocate", {}, "r", ["unrealistic", "too_generic"], primary_score=60.0),
    ]
    review = pr._aggregate("Q?", votes)
    # Deduped
    assert review.all_flags.count("too_generic") == 1
    assert "unrealistic" in review.all_flags


def test_aggregate_all_errors():
    pr = QuestionPanelReviewer()
    votes = [ReviewerVote("domain_expert", {}, "", [], error="boom")]
    review = pr._aggregate("Q?", votes)
    assert review.panel_score is None
    assert review.recommendation == "unknown"


# ── review_question (full) ────────────────────────────────────────────────────

def test_review_question_full_panel():
    pr = QuestionPanelReviewer(concurrency=3)
    responses = [_resp(r) for r in DEFAULT_REVIEWERS]
    with _patch_llm(responses):
        review = pr.review_question("How does parallel_for work?", "oneTBB")
    assert len(review.votes) == 3
    assert review.panel_score is not None
    assert review.recommendation in ("keep", "revise", "drop")


# ── _build_report ─────────────────────────────────────────────────────────────

def test_build_report_summary():
    results = [
        {"panel_score": 80, "recommendation": "keep", "flags": []},
        {"panel_score": 55, "recommendation": "revise", "flags": ["too_generic"]},
        {"panel_score": 30, "recommendation": "drop", "flags": ["opinion_based", "too_broad"]},
    ]
    report = _build_report("oneTBB", results)
    assert report.total == 3
    assert report.summary["keep"] == 1
    assert report.summary["revise"] == 1
    assert report.summary["drop"] == 1
    assert report.summary["mean_panel_score"] is not None
    assert len(report.summary["top_flags"]) > 0


def test_build_report_empty():
    report = _build_report("oneTBB", [])
    assert report.total == 0
    assert report.summary["mean_panel_score"] is None


# ── save report ───────────────────────────────────────────────────────────────

def test_review_questions_saves_json(tmp_path):
    pr = QuestionPanelReviewer(concurrency=3)
    responses = [_resp(r) for r in DEFAULT_REVIEWERS]
    out = tmp_path / "report.json"
    with _patch_llm(responses):
        pr.review_questions(["How to use parallel_for?"], "oneTBB", output_path=out)
    data = json.loads(out.read_text())
    assert data["library_name"] == "oneTBB"
    assert data["total"] == 1
    assert data["questions"][0]["recommendation"] in ("keep", "revise", "drop")


def test_review_questions_saves_flagged_question(tmp_path):
    pr = QuestionPanelReviewer(concurrency=3)
    flagged = json.dumps({"reasoning": "ok", "flags": ["too_generic"], "technical_accuracy": 40,
                          "relevance": 40, "depth": 40, "realism": 40, "clarity": 40,
                          "usefulness": 40, "evaluability": 40, "answerability": 40,
                          "specificity": 40})
    out = tmp_path / "report.json"
    with patch("dokimi.questions.panel_reviewer.llm_call", return_value=flagged):
        pr.review_questions(["Q1?", "Q2?"], "oneTBB", output_path=out)
    data = json.loads(out.read_text())
    assert data["summary"]["top_flags"] == [["too_generic", 2]]
