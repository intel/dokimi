# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Judge-side sampling control.

The answerer's temperature was made settable first; the judge kept sampling at
the provider default, so grading noise stayed uncontrollable. The judge reaches
litellm through its own transport and a panel through ``llm_call``; both needed it.
"""

import pytest

from dokimi.eval import Judge
from dokimi.llm import LLM, Invoker


def test_judge_hands_sampling_params_to_its_client_and_output():
    j = Judge(model="m", provider="openai", temperature=0.0, seed=7)
    assert (j.llm.llm.temperature, j.llm.llm.seed) == (0.0, 7)
    assert j._build_evaluation_output([])["judge_temperature"] == 0.0


def test_the_judge_record_names_the_resolved_models_it_was_judged_by():
    from dokimi.metrics.usage import UsageRecord

    j = Judge(model="sonnet", provider="claude-cli")
    assert "judge_resolved_models" not in j._build_evaluation_output([])
    j._accumulate_usage(UsageRecord(resolved_model="claude-sonnet-5-5"))
    j._accumulate_usage(UsageRecord(resolved_model="claude-sonnet-5-5"))
    assert j._build_evaluation_output([])["judge_resolved_models"] == ["claude-sonnet-5-5"]


def test_the_arms_judge_record_names_the_resolved_models_too():
    # arms run and matrix-run build the judge record in ArmRunner, not in Judge.
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.metrics.usage import UsageRecord
    from dokimi.treatments import BaselineTreatment

    runner, j = ArmRunner([BaselineTreatment()]), Judge(model="opus", provider="claude-cli")
    assert "judge_resolved_models" not in runner.build_output("oneTBB", [], [], judge=j)
    j._accumulate_usage(UsageRecord(resolved_model="claude-opus-5-5"))
    out = runner.build_output("oneTBB", [], [], judge=j)
    assert out["judge_resolved_models"] == ["claude-opus-5-5"]


def test_judge_defaults_leave_sampling_unset():
    j = Judge(model="m", provider="openai")
    assert j.temperature is None
    assert j.seed is None


@pytest.fixture
def completion(monkeypatch):
    """litellm.completion, recording each call's kwargs and answering *payload*."""
    import litellm
    from litellm import ModelResponse

    seen: dict = {"payload": "x"}

    def fake(**kw):
        seen.update(kw)
        return ModelResponse(model=kw["model"], choices=[{"index": 0, "message": {
            "role": "assistant", "content": seen["payload"]}, "finish_reason": "stop"}])

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.delenv("DOKIMI_RESPONSE_CACHE", raising=False)
    return seen


@pytest.mark.parametrize("provider", ["openai", "anthropic", "amazon-bedrock"])
def test_the_judge_transport_forwards_temperature_and_seed(completion, provider):
    Invoker(LLM("m.x", provider, "k", temperature=0.0, seed=5)).invoke("p")
    assert (completion["temperature"], completion["seed"]) == (0.0, 5)


@pytest.mark.parametrize("provider", ["openai", "anthropic", "amazon-bedrock"])
def test_the_judge_transport_omits_sampling_when_unset(completion, provider):
    Invoker(LLM("m.x", provider, "k")).invoke("p")
    assert "temperature" not in completion and "seed" not in completion


def test_the_judge_carries_temperature_to_the_call(completion):
    completion["payload"] = (
        '{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
        '"code_quality":80,"actionability":80,"aggregate":80}'
    )
    j = Judge(model="m", provider="openai", temperature=0.0)
    j.score_answer(library_name="L", question="q", answer="a")
    assert completion["temperature"] == 0.0


# ── panel path ───────────────────────────────────────────────────────────────
# A panel is a judge as far as `arms run --judge-panel` is concerned, and it
# reaches litellm through llm_call rather than the transport the single Judge uses.

_PANEL_PAYLOAD = (
    '{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
    '"code_quality":80,"actionability":80}'
)


def test_judge_config_defaults_leave_sampling_unset():
    from dokimi.eval.panel import JudgeConfig

    c = JudgeConfig(role="technical_expert", model="m", provider="openai")
    assert c.temperature is None
    assert c.seed is None


def test_panel_forwards_per_role_sampling_to_the_call(monkeypatch):
    import dokimi.eval.panel as panel_mod
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    seen = []

    def fake(prompt, model, provider="openai", **kw):
        seen.append(kw)
        return _PANEL_PAYLOAD

    monkeypatch.setattr(panel_mod, "llm_call", fake)
    panel = JudgePanel(judges=[
        JudgeConfig(role="technical_expert", model="m", provider="openai",
                    temperature=0.0, seed=7),
        JudgeConfig(role="developer_advocate", model="m", provider="openai",
                    temperature=0.0, seed=7),
    ])
    panel.score_answer(library_name="L", question="q", answer="a")

    assert len(seen) == 2
    assert all(kw["temperature"] == 0.0 for kw in seen)
    assert all(kw["seed"] == 7 for kw in seen)


def test_panel_omits_sampling_when_unset(monkeypatch):
    import dokimi.eval.panel as panel_mod
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    seen = []

    def fake(prompt, model, provider="openai", **kw):
        seen.append(kw)
        return _PANEL_PAYLOAD

    monkeypatch.setattr(panel_mod, "llm_call", fake)
    panel = JudgePanel(judges=[
        JudgeConfig(role="technical_expert", model="m", provider="openai"),
    ])
    panel.score_answer(library_name="L", question="q", answer="a")

    assert seen and seen[0]["temperature"] is None
    assert seen[0]["seed"] is None


def test_panel_default_roster_carries_sampling(monkeypatch):
    import dokimi.eval.panel as panel_mod
    from dokimi.eval.panel import JudgePanel

    seen = []

    def fake(prompt, model, provider="openai", **kw):
        seen.append(kw)
        return _PANEL_PAYLOAD

    monkeypatch.setattr(panel_mod, "llm_call", fake)
    panel = JudgePanel(default_model="m", default_provider="openai",
                       temperature=0.0)
    panel.score_answer(library_name="L", question="q", answer="a")

    assert seen and all(kw["temperature"] == 0.0 for kw in seen)


# ── parse-failure re-draws at pinned sampling ────────────────────────────────
_GOOD = ('{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
         '"code_quality":80,"actionability":80,"aggregate":80}')


def _litellm_resp(text):
    from types import SimpleNamespace

    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
                           usage=None)


def _deterministic_model(sent):
    """Malformed for the first prompt it sees, valid for any other, like a T=0 model."""
    def completion(model, messages, **kw):
        sent.append((messages[0]["content"], kw))
        return _litellm_resp("not json" if messages[0]["content"] == sent[0][0] else _GOOD)
    return completion


def test_pinned_judge_recovers_a_malformed_reply_with_a_different_redraw(monkeypatch):
    import litellm

    sent = []
    monkeypatch.setattr(litellm, "completion", _deterministic_model(sent))
    j = Judge(model="us.anthropic.x", provider="amazon-bedrock", temperature=0.0)
    assert j.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80
    assert len(sent) == 2 and [kw["temperature"] for _, kw in sent] == [0.0, 0.0]
    assert sent[1][0].startswith(sent[0][0]) and sent[1][0] != sent[0][0]
    assert j._build_evaluation_output([])["judge_metrics"]["redrawn_judgements"] == 1


def test_pinned_judge_first_attempt_is_the_unpinned_request(monkeypatch):
    import litellm

    pinned, unpinned = [], []
    monkeypatch.setattr(litellm, "completion", _deterministic_model(pinned))
    Judge(model="us.anthropic.x", provider="amazon-bedrock", temperature=0.0).score_answer(
        library_name="L", question="q", answer="a")
    monkeypatch.setattr(litellm, "completion",
                        lambda **kw: unpinned.append(kw) or _litellm_resp(_GOOD))
    Judge(model="us.anthropic.x", provider="amazon-bedrock").score_answer(
        library_name="L", question="q", answer="a")
    assert pinned[0][0] == unpinned[0]["messages"][0]["content"]
    assert {k: v for k, v in pinned[0][1].items() if k != "temperature"} == {
        k: v for k, v in unpinned[0].items() if k not in ("messages", "model")}


def test_unpinned_judge_redraws_the_same_prompt(monkeypatch):
    import litellm

    sent = []

    def completion(model, messages, **kw):
        sent.append((messages, kw))
        return _litellm_resp("not json" if len(sent) == 1 else _GOOD)

    monkeypatch.setattr(litellm, "completion", completion)
    j = Judge(model="us.anthropic.x", provider="amazon-bedrock")
    assert j.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80
    assert len(sent) == 2 and sent[0] == sent[1] and "temperature" not in sent[0][1]
    assert j._build_evaluation_output([])["judge_metrics"]["redrawn_judgements"] == 1


def test_pinned_panel_role_recovers_a_malformed_reply_with_a_different_redraw(monkeypatch):
    """At pinned sampling a panel retry of the same prompt gets the same malformed
    reply live; like the single judge, its re-draw appends a format reminder."""
    import litellm
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    sent = []
    monkeypatch.setattr(litellm, "completion", _deterministic_model(sent))
    cfg = JudgeConfig(role="technical_expert", model="gpt-x", provider="openai",
                      temperature=0.0, seed=7)
    vote = JudgePanel(judges=[cfg])._call_judge(cfg, "q", "a", "L", "")
    assert (vote.error, vote.aggregate, len(sent)) == (None, 80.0, 2)
    assert [(kw["temperature"], kw["seed"]) for _, kw in sent] == [(0.0, 7), (0.0, 7)]
    assert sent[1][0].startswith(sent[0][0]) and sent[1][0] != sent[0][0]


def test_unpinned_panel_role_redraws_the_same_prompt(monkeypatch):
    import litellm
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    sent = []

    def completion(model, messages, **kw):
        sent.append(messages)
        return _litellm_resp("not json" if len(sent) == 1 else _GOOD)

    monkeypatch.setattr(litellm, "completion", completion)
    cfg = JudgeConfig(role="technical_expert", model="gpt-x", provider="openai")
    vote = JudgePanel(judges=[cfg])._call_judge(cfg, "q", "a", "L", "")
    assert (vote.error, vote.aggregate, len(sent)) == (None, 80.0, 2)
    assert sent[0] == sent[1]
