# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Transient transport faults must not cost a judge cell its score.

The Bedrock judge path had no retry anywhere: get_llm's _LLM.invoke called
litellm.completion bare, and judge.py's parse-retry loop invoked the model
OUTSIDE its own try block, so a ServiceUnavailableError propagated straight out
and became a null score. A crossed pilot lost 11 of 20 questions to exactly
this.
"""

import pytest

import dokimi.utils as utils
from dokimi.eval import Judge

TRANSIENT = ('litellm.ServiceUnavailableError: BedrockException - {"message":'
             '"The system encountered an unexpected error during processing. '
             'Try your request again."}')

GOOD = ('{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
        '"code_quality":80,"actionability":80,"aggregate":80}')


class _Msg:
    def __init__(self, c):
        self.content = c


class _Choice:
    def __init__(self, c):
        self.message = _Msg(c)


class _Resp:
    def __init__(self, c):
        self.choices = [_Choice(c)]
        self.usage = None


def _flaky(n_fail, payload=GOOD):
    """Fail transiently n_fail times, then succeed. Records call count."""
    state = {"calls": 0}

    def fn(**kw):
        state["calls"] += 1
        if state["calls"] <= n_fail:
            raise Exception(TRANSIENT)
        return _Resp(payload)
    return fn, state


# ── transport layer ─────────────────────────────────────────────────────────
def test_get_llm_retries_transient_and_succeeds(monkeypatch):
    import litellm
    fn, state = _flaky(2)
    monkeypatch.setattr(litellm, "completion", fn)
    llm = utils.get_llm("amazon-bedrock", "us.anthropic.x", api_key="k")
    resp = llm.invoke("p")
    assert resp.content == GOOD
    assert state["calls"] == 3, "should have retried twice then succeeded"


def test_get_llm_gives_up_after_exhausting_retries(monkeypatch):
    import litellm
    fn, state = _flaky(99)
    monkeypatch.setattr(litellm, "completion", fn)
    llm = utils.get_llm("amazon-bedrock", "us.anthropic.x", api_key="k")
    with pytest.raises(Exception, match="ServiceUnavailable"):
        llm.invoke("p")
    assert state["calls"] > 1, "must have attempted more than once"


def test_get_llm_does_not_retry_permanent_errors(monkeypatch):
    import litellm
    state = {"calls": 0}

    def fn(**kw):
        state["calls"] += 1
        raise Exception("AuthenticationError: invalid api key")

    monkeypatch.setattr(litellm, "completion", fn)
    llm = utils.get_llm("amazon-bedrock", "us.anthropic.x", api_key="k")
    with pytest.raises(Exception, match="Authentication"):
        llm.invoke("p")
    assert state["calls"] == 1, "a bad key must not be retried"


# ── judge end to end over the bedrock path ──────────────────────────────────
def test_judge_survives_transient_fault_on_bedrock_path(monkeypatch):
    # The regression that cost the pilot 11 questions.
    import litellm
    fn, state = _flaky(2)
    monkeypatch.setattr(litellm, "completion", fn)
    j = Judge(model="us.anthropic.x", provider="amazon-bedrock")
    out = j.score_answer(library_name="L", question="q", answer="a")
    assert out["aggregate"] == 80
    assert state["calls"] == 3


def test_judge_raises_a_permanent_transport_error_on_the_first_call(monkeypatch):
    import litellm
    import dokimi.eval.judge as judge_mod

    calls = {"n": 0}

    def fn(**kw):
        calls["n"] += 1
        raise Exception("litellm.AuthenticationError: invalid api key")

    sleeps = []
    monkeypatch.setattr(litellm, "completion", fn)
    monkeypatch.setattr(judge_mod.time, "sleep", sleeps.append)
    j = Judge(model="us.anthropic.x", provider="amazon-bedrock")
    with pytest.raises(Exception, match="AuthenticationError"):
        j.score_answer(library_name="L", question="q", answer="a")
    assert calls["n"] == 1
    assert sleeps == []


def test_judge_redraws_a_malformed_reply_without_sleeping(monkeypatch):
    import litellm
    import dokimi.eval.judge as judge_mod

    replies = iter(["not json", GOOD])
    sleeps = []
    monkeypatch.setattr(litellm, "completion", lambda **kw: _Resp(next(replies)))
    monkeypatch.setattr(judge_mod.time, "sleep", sleeps.append)
    j = Judge(model="us.anthropic.x", provider="amazon-bedrock")
    out = j.score_answer(library_name="L", question="q", answer="a")
    assert out["aggregate"] == 80
    assert sleeps == []
