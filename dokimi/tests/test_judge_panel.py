# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for JudgePanel multi-evaluator."""
import json
from unittest.mock import patch

import pytest

from dokimi.eval.panel import (
    DEFAULT_PANEL,
    JudgeConfig,
    JudgePanel,
    JudgeVote,
    PanelVerdict,
    _verdict_to_dict,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _llm_response(reasoning="test", correctness=80, completeness=80,
                  specificity=80, code_quality=80, actionability=80):
    """LLM response WITHOUT aggregate — Python should compute it."""
    return json.dumps({
        "reasoning": reasoning,
        "correctness": correctness,
        "completeness": completeness,
        "specificity": specificity,
        "code_quality": code_quality,
        "actionability": actionability,
    })


def _vote(role="technical_expert", agg=80.0, correctness=80, completeness=80,
          specificity=80, code_quality=80, actionability=80, error=None):
    """Create a JudgeVote with explicit aggregate (Python-computed)."""
    return JudgeVote(
        role=role, model="m", provider="p",
        scores={"correctness": correctness, "completeness": completeness,
                "specificity": specificity, "code_quality": code_quality,
                "actionability": actionability},
        reasoning="r",
        aggregate=agg,
        error=error,
    )


def _patch_llm(responses):
    it = iter(responses)
    return patch("dokimi.eval.panel.llm_call", side_effect=lambda *a, **kw: next(it))


# ── _extract_json ─────────────────────────────────────────────────────────────

def test_extract_json_plain():
    data = JudgePanel._extract_json('{"correctness": 80}')
    assert data["correctness"] == 80


def test_extract_json_with_leading_text():
    raw = 'Here is my evaluation:\n{"correctness": 75, "completeness": 80}'
    data = JudgePanel._extract_json(raw)
    assert data["correctness"] == 75


def test_extract_json_markdown_fence():
    raw = "```json\n{\"correctness\": 70}\n```"
    data = JudgePanel._extract_json(raw)
    assert data["correctness"] == 70


def test_extract_json_no_json_raises():
    with pytest.raises((ValueError, Exception)):
        JudgePanel._extract_json("no json here at all")


# ── _compute_aggregate ────────────────────────────────────────────────────────

def test_compute_aggregate_uses_role_weights():
    scores = {"correctness": 100, "completeness": 0, "specificity": 0,
              "code_quality": 0, "actionability": 0}
    # technical_expert weights correctness×2, total=6.5
    agg = JudgePanel._compute_aggregate("technical_expert", scores)
    expected = round(100 * 2.0 / 6.5, 1)
    assert agg == expected


def test_compute_aggregate_different_per_role():
    scores = {"correctness": 50, "completeness": 50, "specificity": 50,
              "code_quality": 100, "actionability": 100}
    # developer_advocate weights actionability×2, code_quality×1.5
    agg_dev = JudgePanel._compute_aggregate("developer_advocate", scores)
    # technical_expert weights correctness×2
    agg_tech = JudgePanel._compute_aggregate("technical_expert", scores)
    assert agg_dev != agg_tech


def test_compute_aggregate_all_same_gives_same_score():
    scores = {k: 80.0 for k in ("correctness", "completeness", "specificity",
                                  "code_quality", "actionability")}
    for role in DEFAULT_PANEL:
        agg = JudgePanel._compute_aggregate(role, scores)
        assert agg == 80.0


# ── _call_judge ───────────────────────────────────────────────────────────────

def test_call_judge_python_computes_aggregate():
    cfg = JudgeConfig(role="technical_expert", model="gpt-6.1-sol", provider="openai")
    panel = JudgePanel(judges=[cfg])
    # LLM returns no aggregate field — Python must compute it
    llm_resp = json.dumps({"reasoning": "ok", "correctness": 80, "completeness": 80,
                           "specificity": 80, "code_quality": 80, "actionability": 80})
    with _patch_llm([llm_resp]):
        vote = panel._call_judge(cfg, "Q?", "A.", "oneTBB", "ctx")
    assert vote.aggregate is not None
    assert vote.error is None
    assert vote.role == "technical_expert"
    # Aggregate computed by Python, not from JSON
    expected = JudgePanel._compute_aggregate("technical_expert",
                                              {"correctness": 80, "completeness": 80,
                                               "specificity": 80, "code_quality": 80,
                                               "actionability": 80})
    assert vote.aggregate == expected


def test_call_judge_ignores_llm_aggregate():
    """Even if LLM returns aggregate, Python recomputes it."""
    cfg = JudgeConfig(role="technical_expert", model="gpt-6.1-sol", provider="openai")
    panel = JudgePanel(judges=[cfg])
    # LLM returns aggregate=999 (garbage) — must be ignored
    llm_resp = json.dumps({"reasoning": "ok", "correctness": 80, "completeness": 80,
                           "specificity": 80, "code_quality": 80, "actionability": 80,
                           "aggregate": 999})
    with _patch_llm([llm_resp]):
        vote = panel._call_judge(cfg, "Q?", "A.", "oneTBB", "ctx")
    assert vote.aggregate != 999
    assert 0 <= vote.aggregate <= 100


def test_call_judge_clamps_scores():
    cfg = JudgeConfig(role="technical_expert", model="gpt-6.1-sol", provider="openai")
    panel = JudgePanel(judges=[cfg])
    llm_resp = json.dumps({"reasoning": "ok", "correctness": 150, "completeness": -10,
                           "specificity": 80, "code_quality": 80, "actionability": 80})
    with _patch_llm([llm_resp]):
        vote = panel._call_judge(cfg, "Q?", "A.", "oneTBB", "ctx")
    assert vote.scores["correctness"] == 100.0
    assert vote.scores["completeness"] == 0.0


def test_call_judge_handles_leading_text_in_response():
    cfg = JudgeConfig(role="technical_expert", model="gpt-6.1-sol", provider="openai")
    panel = JudgePanel(judges=[cfg])
    llm_resp = 'Sure, here is my evaluation:\n{"reasoning":"ok","correctness":70,"completeness":70,"specificity":70,"code_quality":70,"actionability":70}'
    with _patch_llm([llm_resp]):
        vote = panel._call_judge(cfg, "Q?", "A.", "oneTBB", "ctx")
    assert vote.aggregate is not None
    assert vote.error is None


def test_call_judge_llm_error():
    cfg = JudgeConfig(role="technical_expert", model="gpt-6.1-sol", provider="openai")
    panel = JudgePanel(judges=[cfg])
    with patch("dokimi.eval.panel.llm_call", side_effect=RuntimeError("boom")):
        vote = panel._call_judge(cfg, "Q?", "A.", "oneTBB", "")
    assert vote.aggregate is None
    assert vote.error is not None


# ── _aggregate ────────────────────────────────────────────────────────────────

def test_aggregate_mean_and_std():
    votes = [_vote("technical_expert", 80), _vote("developer_advocate", 60), _vote("doc_reviewer", 70)]
    verdict = JudgePanel()._aggregate(votes)
    assert verdict.mean_aggregate == 70.0
    assert verdict.std_aggregate is not None
    assert 0 <= verdict.agreement_score <= 1


def test_aggregate_perfect_agreement():
    votes = [_vote("technical_expert", 75), _vote("developer_advocate", 75)]
    verdict = JudgePanel()._aggregate(votes)
    assert verdict.std_aggregate == 0.0
    assert verdict.agreement_score == 1.0
    assert not verdict.disagreement_flag


def test_aggregate_high_disagreement():
    votes = [_vote("technical_expert", 20), _vote("developer_advocate", 90)]
    verdict = JudgePanel()._aggregate(votes)
    assert verdict.disagreement_flag


def test_aggregate_all_errors():
    votes = [_vote(agg=None, error="boom"), _vote(agg=None, error="boom")]
    for v in votes:
        v.aggregate = None
    verdict = JudgePanel()._aggregate(votes)
    assert verdict.mean_aggregate is None


def test_aggregate_dimensions():
    votes = [
        _vote(correctness=90, completeness=70, agg=80),
        _vote(correctness=50, completeness=70, agg=60),
    ]
    verdict = JudgePanel()._aggregate(votes)
    assert verdict.mean_dimensions["correctness"] == 70.0
    assert verdict.mean_dimensions["completeness"] == 70.0


# ── evaluate (full) ───────────────────────────────────────────────────────────

def test_evaluate_full_panel():
    judges = [JudgeConfig(r, "gpt-6.1-sol", "openai") for r in DEFAULT_PANEL]
    panel = JudgePanel(judges=judges, concurrency=3)
    responses = [_llm_response(correctness=80) for _ in judges]
    with _patch_llm(responses):
        verdict = panel.evaluate("Q?", "A.", "oneTBB", "ctx")
    assert verdict.mean_aggregate is not None
    assert len(verdict.votes) == 3


def test_evaluate_partial_failure():
    judges = [JudgeConfig("technical_expert", "m", "openai"),
              JudgeConfig("developer_advocate", "m", "openai")]
    panel = JudgePanel(judges=judges, concurrency=2)
    it = iter([_llm_response(), "bad json {{{}"])
    with patch("dokimi.eval.panel.llm_call", side_effect=lambda *a, **kw: next(it)):
        verdict = panel.evaluate("Q?", "A.", "oneTBB", "")
    assert len(verdict.valid_votes) == 1


# ── defaults ──────────────────────────────────────────────────────────────────

def test_default_panel_has_three_roles():
    assert len(DEFAULT_PANEL) == 3
    assert "technical_expert" in DEFAULT_PANEL


def test_default_judges_created_without_config():
    panel = JudgePanel()
    assert len(panel.judges) == 3
    assert {j.role for j in panel.judges} == set(DEFAULT_PANEL)


# ── _verdict_to_dict ──────────────────────────────────────────────────────────

def test_verdict_to_dict_none():
    assert _verdict_to_dict(None) is None


def test_verdict_to_dict_structure():
    votes = [JudgeVote("technical_expert", "m", "p", {"aggregate": 80}, "r", aggregate=80.0)]
    verdict = PanelVerdict(votes=votes, mean_aggregate=80.0, std_aggregate=0.0,
                           agreement_score=1.0, mean_dimensions={"correctness": 80.0})
    d = _verdict_to_dict(verdict)
    assert d["aggregate"] == 80.0
    assert d["agreement_score"] == 1.0
    assert not d["disagreement_flag"]
