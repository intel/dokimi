# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Panel quorum, ground-truth forwarding and identity (#407 phase 0, item B3)."""
import json
from unittest.mock import patch

from dokimi.eval.panel import (
    DEFAULT_PANEL,
    ROLE_WEIGHTS,
    JudgeConfig,
    JudgePanel,
    JudgeVote,
    _verdict_to_dict,
)
from dokimi.eval.judge import MAX_GROUND_TRUTH_CHARS


def _reply(correctness=80):
    return json.dumps({
        "reasoning": "r", "correctness": correctness, "completeness": 80,
        "specificity": 80, "code_quality": 80, "actionability": 80,
    })


def _vote(role, agg=None, error=None):
    scores = {} if agg is None else {k: agg for k in
                                     ("correctness", "completeness", "specificity",
                                      "code_quality", "actionability")}
    return JudgeVote(role=role, model="m1", provider="openai", scores=scores,
                     reasoning="", aggregate=agg, error=error)


def _three(models=("m1", "m1", "m1"), temperature=None):
    return [JudgeConfig(role=r, model=m, provider="openai", temperature=temperature)
            for r, m in zip(DEFAULT_PANEL, models, strict=True)]


# Each role's intro is unique in its prompt; a role listed in `good` answers, the rest raise.
_INTRO = {
    "technical_expert": "STRICT TECHNICAL EXPERT",
    "developer_advocate": "DEVELOPER ADVOCATE",
    "doc_reviewer": "DOCUMENTATION QUALITY REVIEWER",
}


def _fake_llm(good):
    def fake(prompt, **kwargs):
        if any(_INTRO[r] in prompt for r in good):
            return _reply()
        raise RuntimeError("boom")
    return fake


def _prompts_sent(panel, **score_kwargs):
    sent = []

    def fake(prompt, **kwargs):
        sent.append(prompt)
        return _reply()

    with patch("dokimi.eval.panel.llm_call", side_effect=fake):
        panel.score_answer("oneTBB", "How do I loop?", "Use parallel_for.", **score_kwargs)
    return sent


# ── quorum ────────────────────────────────────────────────────────────────────

def test_one_valid_vote_of_three_is_insufficient_evidence_not_agreement():
    verdict = JudgePanel(judges=_three())._aggregate([
        _vote("technical_expert", agg=70.0),
        _vote("developer_advocate", error="boom"),
        _vote("doc_reviewer", error="boom"),
    ])
    assert verdict.insufficient_evidence
    assert verdict.mean_aggregate is None
    assert verdict.std_aggregate is None
    assert verdict.agreement_score is None
    assert not verdict.disagreement_flag
    assert verdict.mean_dimensions == {}


def test_majority_of_panelists_valid_is_enough():
    verdict = JudgePanel(judges=_three())._aggregate([
        _vote("technical_expert", agg=70.0),
        _vote("developer_advocate", agg=70.0),
        _vote("doc_reviewer", error="boom"),
    ])
    assert not verdict.insufficient_evidence
    assert verdict.mean_aggregate == 70.0
    assert verdict.agreement_score == 1.0


def test_half_of_four_panelists_is_not_a_majority():
    judges = [JudgeConfig(role=r, model="m1", provider="openai") for r in
              ("technical_expert", "developer_advocate", "doc_reviewer", "technical_expert")]
    verdict = JudgePanel(judges=judges)._aggregate([
        _vote("technical_expert", agg=70.0), _vote("developer_advocate", agg=40.0),
        _vote("doc_reviewer", error="boom"), _vote("technical_expert", error="boom"),
    ])
    assert verdict.insufficient_evidence
    assert verdict.mean_aggregate is None


def test_single_valid_vote_has_no_measurable_spread():
    verdict = JudgePanel(judges=[JudgeConfig("technical_expert", "m1", "openai")])._aggregate([_vote("technical_expert", agg=75.0)])
    assert verdict.mean_aggregate == 75.0
    assert verdict.std_aggregate is None
    assert verdict.agreement_score is None


def test_score_answer_reports_insufficient_evidence_as_an_error():
    panel = JudgePanel(judges=_three())
    with patch("dokimi.eval.panel.llm_call", side_effect=_fake_llm(["technical_expert"])):
        out = panel.score_answer("oneTBB", "Q?", "A.")
    assert out["aggregate"] is None
    assert out["panel"]["n_valid"] == 1
    assert "insufficient evidence" in out["error"]


def test_score_answer_panel_fields_follow_the_grounding_and_the_quorum():
    panel = JudgePanel(judges=_three())
    with patch("dokimi.eval.panel.llm_call",
               side_effect=_fake_llm(["technical_expert", "developer_advocate"])):
        ok = panel.score_answer("oneTBB", "Q?", "A.", ground_truth="REF")
    assert ok["panel"]["grounded"] is True
    assert ok["panel"]["quorum"] == 2
    assert ok["panel"]["insufficient_evidence"] is False
    assert "error" not in ok
    with patch("dokimi.eval.panel.llm_call", side_effect=_fake_llm(["technical_expert"])):
        bad = panel.score_answer("oneTBB", "Q?", "A.")
    assert bad["panel"]["grounded"] is False
    assert bad["panel"]["insufficient_evidence"] is True


def test_quorum_failed_verdict_dict_carries_the_error_score_answer_gives():
    panel = JudgePanel(judges=_three())
    with patch("dokimi.eval.panel.llm_call", side_effect=_fake_llm(["technical_expert"])):
        cell = _verdict_to_dict(panel.evaluate(question="Q?", answer="A.", library_name="oneTBB"))
    assert cell["aggregate"] is None
    assert cell["insufficient_evidence"] is True
    assert cell["error"] == "insufficient evidence: 1 of 3 panelists valid, quorum is 2"
    with patch("dokimi.eval.panel.llm_call", side_effect=_fake_llm([])):
        failed = _verdict_to_dict(panel.evaluate(question="Q?", answer="A.", library_name="oneTBB"))
    assert failed["error"] == "all panel judges failed"


def test_a_valid_verdict_dict_has_no_error_key():
    panel = JudgePanel(judges=_three())
    with patch("dokimi.eval.panel.llm_call", side_effect=_fake_llm(list(_INTRO))):
        cell = _verdict_to_dict(panel.evaluate(question="Q?", answer="A.", library_name="oneTBB"))
    assert cell["insufficient_evidence"] is False
    assert "error" not in cell


# ── ground_truth ──────────────────────────────────────────────────────────────

def test_ground_truth_reaches_every_role_prompt():
    prompts = _prompts_sent(JudgePanel(judges=_three()), ground_truth="REF-TEXT-ALPHA")
    assert len(prompts) == 3
    assert all("REF-TEXT-ALPHA" in p for p in prompts)


def test_flipping_ground_truth_changes_the_prompt():
    panel = JudgePanel(judges=_three())
    grounded = _prompts_sent(panel, ground_truth="REF-TEXT-ALPHA")
    plain = _prompts_sent(panel, ground_truth=None)
    assert all("REF-TEXT-ALPHA" not in p for p in plain)
    assert [g != p for g, p in zip(grounded, plain, strict=True)] == [True, True, True]


def test_ground_truth_is_clipped_like_the_single_judge():
    long_ref = "x" * (MAX_GROUND_TRUTH_CHARS * 2)
    prompt = _prompts_sent(JudgePanel(judges=_three()), ground_truth=long_ref)[0]
    assert "x" * 100 in prompt
    assert "x" * (MAX_GROUND_TRUTH_CHARS + 1) not in prompt


def test_evaluate_answers_forwards_the_record_ground_truth(tmp_path):
    answers = [{
        "question_id": "q0", "question_text": "How?", "library_name": "oneTBB",
        "ground_truth_chunk": "REF-TEXT-BETA",
        "with_docs": {"answer": "Use parallel_for.", "retrieved_docs": [], "doc_source": "context7"},
        "without_docs": {"answer": "Use a loop."},
    }]
    sent = []

    def fake(prompt, **kwargs):
        sent.append(prompt)
        return _reply()

    with patch("dokimi.eval.panel.llm_call", side_effect=fake):
        JudgePanel(judges=_three()).evaluate_answers(answers, library_name="oneTBB")
    with_docs_prompts = [p for p in sent if "Use parallel_for." in p]
    without_docs_prompts = [p for p in sent if "Use a loop." in p]
    assert with_docs_prompts and all("REF-TEXT-BETA" in p for p in with_docs_prompts)
    assert without_docs_prompts and all("REF-TEXT-BETA" in p for p in without_docs_prompts)


def test_ungrounded_prompt_has_no_reference_block():
    prompts = _prompts_sent(JudgePanel(judges=_three()), ground_truth=None)
    assert all("Reference (" not in p for p in prompts)
    assert all("contradicts is incorrect" not in p for p in prompts)


def test_placeholder_text_in_the_answer_is_sent_verbatim():
    sent = []

    def fake(prompt, **kwargs):
        sent.append(prompt)
        return _reply()

    with patch("dokimi.eval.panel.llm_call", side_effect=fake):
        JudgePanel(judges=_three()).score_answer(
            "oneTBB", "Q?", "Keep __CONTEXT__ and __GROUND_TRUTH__ as written.",
            context="DOCS-ALPHA", ground_truth="REF-TEXT-GAMMA")
    assert len(sent) == 3
    assert all("Keep __CONTEXT__ and __GROUND_TRUTH__ as written." in p for p in sent)
    assert all("REF-TEXT-GAMMA" in p and "DOCS-ALPHA" in p for p in sent)


# ── identity ──────────────────────────────────────────────────────────────────

def test_swapping_models_between_roles_changes_the_model_identity():
    a = JudgePanel(judges=[JudgeConfig("technical_expert", "m1", "openai"),
                           JudgeConfig("developer_advocate", "m2", "openai")])
    b = JudgePanel(judges=[JudgeConfig("technical_expert", "m2", "openai"),
                           JudgeConfig("developer_advocate", "m1", "openai")])
    assert a.model != b.model


def test_swapping_models_between_roles_changes_the_prompt_hash():
    a = JudgePanel(judges=[JudgeConfig("technical_expert", "m1", "openai"),
                           JudgeConfig("developer_advocate", "m2", "openai")])
    b = JudgePanel(judges=[JudgeConfig("technical_expert", "m2", "openai"),
                           JudgeConfig("developer_advocate", "m1", "openai")])
    assert a.prompt_sha256 != b.prompt_sha256


def test_temperature_changes_the_prompt_hash():
    unpinned = JudgePanel(judges=_three(temperature=None))
    pinned = JudgePanel(judges=_three(temperature=0.0))
    assert unpinned.prompt_sha256 != pinned.prompt_sha256


def test_aggregation_policy_changes_the_prompt_hash():
    before = JudgePanel(judges=_three()).prompt_sha256
    changed = {**ROLE_WEIGHTS, "doc_reviewer": {**ROLE_WEIGHTS["doc_reviewer"], "correctness": 5.0}}
    with patch.dict("dokimi.eval.panel.ROLE_WEIGHTS", changed, clear=True):
        after = JudgePanel(judges=_three()).prompt_sha256
    assert before != after


def test_grounding_block_text_changes_the_prompt_hash(monkeypatch):
    before = JudgePanel(judges=_three()).prompt_sha256
    monkeypatch.setattr("dokimi.eval.panel._GROUND_BLOCK",
                        "Reference (reworded).\n__GROUND_TRUTH__\nA claim the reference supports is fine.\n")
    assert JudgePanel(judges=_three()).prompt_sha256 != before


def test_ground_truth_clip_limit_changes_the_prompt_hash(monkeypatch):
    before = JudgePanel(judges=_three()).prompt_sha256
    monkeypatch.setattr("dokimi.eval.panel.MAX_GROUND_TRUTH_CHARS", MAX_GROUND_TRUTH_CHARS // 2)
    assert JudgePanel(judges=_three()).prompt_sha256 != before


def test_disagreement_threshold_changes_the_prompt_hash(monkeypatch):
    before = JudgePanel(judges=_three()).prompt_sha256
    monkeypatch.setattr("dokimi.eval.panel.DISAGREEMENT_STD", 20.0)
    assert JudgePanel(judges=_three()).prompt_sha256 != before


def test_seed_and_provider_change_the_prompt_hash():
    base = JudgePanel(judges=_three()).prompt_sha256
    seeded = JudgePanel(judges=[JudgeConfig(r, "m1", "openai", seed=7) for r in DEFAULT_PANEL])
    other = JudgePanel(judges=[JudgeConfig(r, "m1", "anthropic") for r in DEFAULT_PANEL])
    assert seeded.prompt_sha256 != base
    assert other.prompt_sha256 != base


def test_panel_summary_handles_a_single_judge_with_no_agreement(tmp_path, monkeypatch, capsys):
    """`eval score panel` with one judge has no spread: the summary prints n/a, not a crash."""
    from types import SimpleNamespace

    import litellm

    from dokimi.cli import build_parser

    def completion(**kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=_reply()))],
                               usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps([{
        "question_id": "Q1", "question_text": "How?", "question": "How?",
        "with_docs": {"answer": "with docs"}, "without_docs": {"answer": "without docs"},
    }]), encoding="utf-8")
    out = tmp_path / "panel.json"
    args = build_parser().parse_args([
        "eval", "score", "panel", "--product", "L", "--answers", str(answers),
        "--output", str(out), "--provider", "amazon-bedrock", "--model", "us.anthropic.x",
        "--roles", "technical_expert", "--concurrency", "1"])
    args.func(args)
    assert "Mean agreement score:     n/a" in capsys.readouterr().out
    assert json.loads(out.read_text())["evaluations"][0]["with_docs"]["std"] is None


def test_panel_summary_counts_the_questions_left_out_for_no_quorum(tmp_path, monkeypatch, capsys):
    """A question without a quorum is left out of the mean; the summary says how many."""
    from types import SimpleNamespace

    import litellm

    from dokimi.cli import build_parser

    def completion(**kwargs):
        text = " ".join(m["content"] for m in kwargs["messages"])
        if "bad docs" in text and "STRICT TECHNICAL EXPERT" not in text:
            raise RuntimeError("boom")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=_reply()))],
                               usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps([
        {"question_id": "Q1", "question_text": "How?", "question": "How?",
         "with_docs": {"answer": "good docs"}, "without_docs": {"answer": "without docs"}},
        {"question_id": "Q2", "question_text": "Why?", "question": "Why?",
         "with_docs": {"answer": "bad docs"}, "without_docs": {"answer": "without docs"}},
    ]), encoding="utf-8")
    out = tmp_path / "panel.json"
    args = build_parser().parse_args([
        "eval", "score", "panel", "--product", "L", "--answers", str(answers),
        "--output", str(out), "--provider", "amazon-bedrock", "--model", "us.anthropic.x",
        "--roles", "technical_expert,developer_advocate,doc_reviewer", "--concurrency", "1"])
    args.func(args)
    printed = capsys.readouterr().out
    assert "Questions left out (no quorum or no answer): 1/2" in printed
    assert "Disagreement flags:       0/1 questions" in printed

    all_bad = tmp_path / "all_bad.json"
    all_bad.write_text(json.dumps([
        {"question_id": q, "question_text": "Why?", "question": "Why?",
         "with_docs": {"answer": "bad docs"}, "without_docs": {"answer": "without docs"}}
        for q in ("Q1", "Q2")]), encoding="utf-8")
    args = build_parser().parse_args([
        "eval", "score", "panel", "--product", "L", "--answers", str(all_bad),
        "--output", str(tmp_path / "panel_all_bad.json"), "--provider", "amazon-bedrock",
        "--model", "us.anthropic.x", "--roles", "technical_expert,developer_advocate,doc_reviewer",
        "--concurrency", "1"])
    args.func(args)
    assert "Questions left out (no quorum or no answer): 2/2" in capsys.readouterr().out
