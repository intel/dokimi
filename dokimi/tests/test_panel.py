# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for eval/panel.py — multi-judge panel with role-diverse judges."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


from dokimi.artifacts import validate_artifact
from dokimi.eval.panel import (
    DEFAULT_PANEL,
    ROLE_WEIGHTS,
    SCORE_FIELDS,
    JudgeConfig,
    JudgePanel,
    JudgeVote,
    PanelVerdict,
    _verdict_to_dict,
)

# _aggregate is a method on JudgePanel — expose via helper for unit tests
_panel_instance = JudgePanel.__new__(JudgePanel)
_panel_instance.judges = []


def _aggregate(votes):
    return _panel_instance._aggregate(votes)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_scores(value: float = 80.0) -> dict:
    return {k: value for k in SCORE_FIELDS}


def _make_vote(role="technical_expert", agg=80.0, error=None) -> JudgeVote:
    return JudgeVote(
        role=role, model="gpt-4o-mini", provider="openai",
        scores=_make_scores(agg), reasoning="ok", aggregate=agg, error=error,
    )


# ── ROLE_WEIGHTS & aggregate computation ──────────────────────────────────────

def test_role_weights_cover_all_roles():
    for role in DEFAULT_PANEL:
        assert role in ROLE_WEIGHTS, f"Missing weights for role: {role}"


def test_role_weights_cover_all_score_fields():
    for role, weights in ROLE_WEIGHTS.items():
        for field in SCORE_FIELDS:
            assert field in weights, f"Role '{role}' missing weight for '{field}'"


def test_compute_aggregate_uniform_scores():
    """Uniform scores → aggregate equals that score regardless of weights."""
    panel = JudgePanel()
    for role in DEFAULT_PANEL:
        agg = panel._compute_aggregate(role, _make_scores(70.0))
        assert abs(agg - 70.0) < 0.1, f"Role {role}: expected 70.0, got {agg}"


def test_compute_aggregate_correctness_heavy_for_expert():
    """technical_expert weights correctness 2x — high correctness raises aggregate."""
    panel = JudgePanel()
    scores_high_correct = {k: 50.0 for k in SCORE_FIELDS}
    scores_high_correct["correctness"] = 100.0
    agg = panel._compute_aggregate("technical_expert", scores_high_correct)
    # With correctness=100 and rest=50, weighted mean > 50
    assert agg > 55.0


def test_compute_aggregate_unknown_role_uses_equal_weights():
    panel = JudgePanel()
    agg = panel._compute_aggregate("unknown_role", _make_scores(60.0))
    assert abs(agg - 60.0) < 0.1


# ── PanelVerdict aggregation ──────────────────────────────────────────────────

def test_aggregate_empty_votes():
    verdict = _aggregate([])
    assert verdict.mean_aggregate is None
    assert verdict.std_aggregate is None
    assert verdict.agreement_score is None


def test_aggregate_single_vote():
    votes = [_make_vote(agg=75.0)]
    verdict = _aggregate(votes)
    assert verdict.mean_aggregate == 75.0
    assert verdict.std_aggregate == 0.0
    assert verdict.agreement_score == 1.0


def test_aggregate_multiple_votes_mean():
    votes = [_make_vote(agg=60.0), _make_vote(agg=80.0), _make_vote(agg=100.0)]
    verdict = _aggregate(votes)
    assert abs(verdict.mean_aggregate - 80.0) < 0.2


def test_aggregate_high_std_lowers_agreement():
    votes = [_make_vote(agg=0.0), _make_vote(agg=100.0)]
    verdict = _aggregate(votes)
    assert verdict.agreement_score is not None
    assert verdict.agreement_score < 0.5


def test_aggregate_low_std_high_agreement():
    votes = [_make_vote(agg=78.0), _make_vote(agg=80.0), _make_vote(agg=82.0)]
    verdict = _aggregate(votes)
    assert verdict.agreement_score is not None
    assert verdict.agreement_score > 0.8


def test_aggregate_disagreement_flag_large_std():
    votes = [_make_vote(agg=20.0), _make_vote(agg=90.0)]
    verdict = _aggregate(votes)
    assert verdict.disagreement_flag is True


def test_aggregate_no_disagreement_flag_small_std():
    votes = [_make_vote(agg=78.0), _make_vote(agg=82.0)]
    verdict = _aggregate(votes)
    assert verdict.disagreement_flag is False


def test_aggregate_skips_error_votes():
    votes = [_make_vote(agg=70.0), _make_vote(agg=None, error="API error")]
    verdict = _aggregate(votes)
    assert verdict.mean_aggregate == 70.0
    assert len(verdict.valid_votes) == 1


def test_aggregate_all_error_votes():
    votes = [_make_vote(agg=None, error="fail"), _make_vote(agg=None, error="fail")]
    verdict = _aggregate(votes)
    assert verdict.mean_aggregate is None


def test_aggregate_mean_dimensions():
    scores_a = {k: 60.0 for k in SCORE_FIELDS}
    scores_b = {k: 80.0 for k in SCORE_FIELDS}
    votes = [
        JudgeVote("technical_expert", "m", "p", scores_a, "", 60.0),
        JudgeVote("doc_reviewer", "m", "p", scores_b, "", 80.0),
    ]
    verdict = _aggregate(votes)
    for dim in SCORE_FIELDS:
        assert abs(verdict.mean_dimensions[dim] - 70.0) < 0.2


# ── JudgePanel._call_judge ────────────────────────────────────────────────────

FAKE_LLM_RESPONSE = json.dumps({
    "reasoning": "Good answer",
    "correctness": 80, "completeness": 75, "specificity": 70,
    "code_quality": 90, "actionability": 85,
})


def test_call_judge_success():
    panel = JudgePanel()
    cfg = JudgeConfig(role="technical_expert", model="gpt-4o-mini", provider="openai")
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        vote = panel._call_judge(cfg, "How to use TBB?", "Use parallel_for.", "oneTBB", "")
    assert vote.error is None
    assert vote.scores["correctness"] == 80.0
    assert vote.aggregate is not None
    assert 0.0 <= vote.aggregate <= 100.0


def test_call_judge_clamps_scores():
    """Scores outside 0-100 should be clamped."""
    panel = JudgePanel()
    cfg = JudgeConfig(role="developer_advocate", model="gpt-4o-mini", provider="openai")
    out_of_range = json.dumps({
        "reasoning": "test", "correctness": 150, "completeness": -10,
        "specificity": 80, "code_quality": 80, "actionability": 80,
    })
    with patch("dokimi.eval.panel.llm_call", return_value=out_of_range):
        vote = panel._call_judge(cfg, "q", "a", "oneTBB", "")
    assert vote.scores["correctness"] == 100.0
    assert vote.scores["completeness"] == 0.0


def test_call_judge_error_returns_error_vote():
    panel = JudgePanel()
    cfg = JudgeConfig(role="doc_reviewer", model="gpt-4o-mini", provider="openai")
    with patch("dokimi.eval.panel.llm_call", side_effect=RuntimeError("API down")):
        vote = panel._call_judge(cfg, "q", "a", "oneTBB", "")
    assert vote.error == "API down"
    assert vote.aggregate is None


# ── JudgePanel.evaluate ───────────────────────────────────────────────────────

def test_evaluate_runs_all_judges():
    panel = JudgePanel(judges=[
        JudgeConfig("technical_expert", "gpt-4o-mini", "openai"),
        JudgeConfig("developer_advocate", "claude-sonnet-4", "anthropic"),
        JudgeConfig("doc_reviewer", "gemini-2.0-flash", "google"),
    ])
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        verdict = panel.evaluate("How to schedule tasks?", "Use task_group.", "oneTBB")
    assert len(verdict.votes) == 3
    assert verdict.mean_aggregate is not None


def test_evaluate_gemini_provider_accepted():
    """Gemini provider config should not raise on construction."""
    cfg = JudgeConfig(role="doc_reviewer", model="gemini-2.0-flash", provider="google")
    panel = JudgePanel(judges=[cfg])
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        verdict = panel.evaluate("q", "a", "oneTBB")
    assert verdict.mean_aggregate is not None


# ── _verdict_to_dict ──────────────────────────────────────────────────────────

def test_verdict_to_dict_none():
    assert _verdict_to_dict(None) is None


def test_verdict_to_dict_structure():
    verdict = PanelVerdict(
        votes=[_make_vote()], mean_aggregate=80.0, std_aggregate=0.0,
        agreement_score=1.0, mean_dimensions={k: 80.0 for k in SCORE_FIELDS},
    )
    d = _verdict_to_dict(verdict)
    assert d["aggregate"] == 80.0
    assert d["std"] == 0.0
    assert d["agreement_score"] == 1.0
    assert "votes" in d
    assert "dimensions" in d


# ── evaluate_answers end-to-end ───────────────────────────────────────────────

def _fake_answers(n=2):
    """Answer records in the shape ``Answerer`` writes (validated as answers.v1)."""
    answers = [
        {
            "question_id": f"q{i}",
            "question_text": f"Question {i}?",
            "library_name": "oneTBB",
            "with_docs": {
                "answer": f"Answer {i} with context.",
                "retrieved_docs": [
                    {"source": "context7", "snippet": f"Doc snippet {i}.", "relevance_score": 0.9},
                ],
                "doc_source": "context7",
            },
            "without_docs": {"answer": f"Answer {i} baseline."},
        }
        for i in range(n)
    ]
    validate_artifact("answers", {"schema_version": "answers.v1", "answers": answers})
    return answers


def _real_answerer_output(tmp_path):
    """An answers.v1 artifact written by the real ``Answerer`` (LLM and MCP mocked)."""
    from dokimi.eval.answerer import Answerer
    from dokimi.eval.reranker import SimpleReranker

    mcp = MagicMock()
    mcp.get_library_docs.return_value = [{
        "content": "Use tbb::parallel_for(range, body) to split a loop. " * 10,
        "source": "context7",
    }]
    answerer = Answerer.__new__(Answerer)
    answerer.mcp_client = mcp
    answerer.model, answerer.provider, answerer.api_key = "m", "openai", None
    answerer.top_k, answerer.debug_retrieval = 5, False
    answerer.reranker = SimpleReranker(threshold=0.0)
    questions = [{"id": "q_pf", "text": "How do I parallelize a loop with parallel_for?"}]
    with patch("dokimi.eval.answerer.llm_call_with_usage",
               return_value=("an answer", {"prompt_tokens": 1, "completion_tokens": 1,
                                           "total_tokens": 2})):
        answers = answerer.generate_answers("oneTBB", "uxlfoundation/oneTBB", questions)
    out = tmp_path / "answers.json"
    answerer.save_answers(answers, out)
    data = json.loads(out.read_text())
    validate_artifact("answers", data)
    return data


def test_panel_judges_real_answerer_output_with_question_and_context(tmp_path):
    """The panel read ``question``/``retrieved_docs[].content``, which the answerer
    never writes, so every answer was judged on an empty question and no context."""
    data = _real_answerer_output(tmp_path)
    record = data["answers"][0]
    snippet = record["with_docs"]["retrieved_docs"][0]["snippet"]
    prompts = []

    def fake_llm(prompt, **kw):
        prompts.append(prompt)
        return FAKE_LLM_RESPONSE

    panel = JudgePanel(judges=[JudgeConfig("doc_reviewer", "gpt-4o-mini", "openai")])
    with patch("dokimi.eval.panel.llm_call", side_effect=fake_llm):
        results = panel.evaluate_answers(data["answers"], library_name="oneTBB")

    assert results[0]["question"] == record["question_text"]
    with_prompt, without_prompt = prompts  # one judge, two arms (sequential)
    assert f"Question: {record['question_text']}\n" in with_prompt
    assert f"Question: {record['question_text']}\n" in without_prompt
    assert f"Documentation context: {snippet}" in with_prompt
    assert "Documentation context: (No documentation provided)" in without_prompt


def test_evaluate_answers_returns_all():
    panel = JudgePanel()
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        results = panel.evaluate_answers(_fake_answers(3), library_name="oneTBB")
    assert len(results) == 3


def test_evaluate_answers_structure():
    panel = JudgePanel()
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        results = panel.evaluate_answers(_fake_answers(1), library_name="oneTBB")
    r = results[0]
    assert "question_id" in r
    assert "with_docs" in r
    assert "without_docs" in r
    assert "delta" in r
    assert r["panel_size"] == len(DEFAULT_PANEL)


def test_evaluate_answers_delta_zero_when_same_score():
    """Same mocked LLM response → same score → delta ≈ 0."""
    panel = JudgePanel()
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        results = panel.evaluate_answers(_fake_answers(1), library_name="oneTBB")
    assert results[0]["delta"] == 0.0


def test_evaluate_answers_incremental_save(tmp_path):
    panel = JudgePanel()
    out = tmp_path / "eval_panel.json"
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        panel.evaluate_answers(_fake_answers(2), library_name="oneTBB", output_path=out)
    assert out.exists()
    data = json.loads(out.read_text())
    assert data["total_evaluations"] == 2
    assert "panel_config" in data


def test_evaluate_answers_limit(tmp_path):
    panel = JudgePanel()
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        results = panel.evaluate_answers(_fake_answers(5), library_name="oneTBB", limit=2)
    assert len(results) == 2


def test_evaluate_answers_records_retrieval_hit():
    """The trust gate reads retrieval_hit off the eval record; the panel writes it too."""
    answers = _fake_answers(2)
    answers[0]["with_docs"].update(
        retrieved_docs=[{"source": "context7", "content": "parallel_for ..."}],
        doc_source="context7",
    )
    answers[1]["with_docs"].update(
        answer="[FALLBACK: No relevant documentation found]",
        retrieved_docs=[], doc_source="fallback_none",
    )
    panel = JudgePanel()
    with patch("dokimi.eval.panel.llm_call", return_value=FAKE_LLM_RESPONSE):
        results = panel.evaluate_answers(answers, library_name="oneTBB")
    assert [r["retrieval_hit"] for r in results] == [True, False]
