# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Judge-side sampling control.

The answerer's temperature was made settable first; the judge kept sampling at
the provider default, so grading noise stayed uncontrollable. The judge reaches
litellm by three different paths and all three needed the same treatment.
"""

import dokimi.llm as llm_mod
from dokimi.eval import Judge


def test_judge_accepts_and_stores_sampling_params():
    j = Judge(model="m", provider="openai", temperature=0.0, seed=7)
    assert j.temperature == 0.0
    assert j.seed == 7


def test_judge_defaults_leave_sampling_unset():
    j = Judge(model="m", provider="openai")
    assert j.temperature is None
    assert j.seed is None


def test_chat_shim_forwards_temperature(monkeypatch):
    seen = {}

    def fake(prompt, model, provider="openai", api_key=None, **kw):
        seen.update(kw)
        return "ok", None

    monkeypatch.setattr(llm_mod, "llm_call_with_usage", fake)
    llm_mod.ChatOpenAI(model="m", temperature=0.0, seed=3).invoke("p")
    assert seen["temperature"] == 0.0
    assert seen["seed"] == 3


def test_chat_shim_omits_sampling_when_unset(monkeypatch):
    seen = {}

    def fake(prompt, model, provider="openai", api_key=None, **kw):
        seen.update(kw)
        return "ok", None

    monkeypatch.setattr(llm_mod, "llm_call_with_usage", fake)
    llm_mod.ChatAnthropic(model="m").invoke("p")
    assert seen.get("temperature") is None
    assert seen.get("seed") is None


def test_judge_openai_path_carries_temperature_to_the_call(monkeypatch):
    # Judge -> ChatOpenAI -> llm_call_with_usage
    seen = {}

    payload = (
        '{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
        '"code_quality":80,"actionability":80,"aggregate":80}'
    )

    def fake(prompt, model, provider="openai", api_key=None, **kw):
        seen.update(kw)
        return payload, None

    monkeypatch.setattr(llm_mod, "llm_call_with_usage", fake)
    j = Judge(model="m", provider="openai", temperature=0.0)
    j.score_answer(library_name="L", question="q", answer="a")
    assert seen["temperature"] == 0.0


# ── panel path ───────────────────────────────────────────────────────────────
# A panel is a judge as far as `arms run --judge-panel` is concerned, and it
# reaches litellm through llm_call rather than the shims the single Judge uses.

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


def test_get_llm_forwards_temperature_to_completion(monkeypatch):
    from dokimi import utils

    seen = {}

    class _Resp:
        class _C:
            class _M:
                content = "x"
            message = _M()
        choices = [_C()]
        usage = None

    def fake_completion(**kw):
        seen.update(kw)
        return _Resp()

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    llm = utils.get_llm("amazon-bedrock", "us.anthropic.x", api_key="k",
                        temperature=0.0, seed=5)
    llm.invoke("p")
    assert seen["temperature"] == 0.0
    assert seen["seed"] == 5


def test_get_llm_omits_sampling_when_unset(monkeypatch):
    from dokimi import utils

    seen = {}

    class _Resp:
        class _C:
            class _M:
                content = "x"
            message = _M()
        choices = [_C()]
        usage = None

    def fake_completion(**kw):
        seen.update(kw)
        return _Resp()

    import litellm
    monkeypatch.setattr(litellm, "completion", fake_completion)
    utils.get_llm("amazon-bedrock", "us.anthropic.x", api_key="k").invoke("p")
    assert "temperature" not in seen
    assert "seed" not in seen


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
