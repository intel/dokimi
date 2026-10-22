# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The judge's two response-cache defects: the `get_llm` path (Bedrock, Vertex,
OpenRouter) bypassed the cache and had no retry, and a judge retry after an
unparseable reply replayed the same cached reply instead of drawing a new one."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dokimi import caching
from dokimi.eval.judge import Judge
from dokimi.utils import get_llm

GOOD = (
    '{"correctness": 80, "completeness": 80, "specificity": 80, '
    '"code_quality": 80, "actionability": 80, "aggregate": 0}'
)


def _completion(texts):
    """A fake litellm.completion returning ``texts`` in order (an Exception item is raised)."""
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        item = texts[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, total_tokens=12),
            model="m",
        )

    return completion, calls


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    return tmp_path


def test_draw_zero_keeps_existing_cache_keys():
    base = {"stream": False, "max_tokens": None}
    assert caching.draw_extra(base, 0) is base
    k0 = caching.response_cache_key(litellm_model="m", messages=[], extra=base)
    k1 = caching.response_cache_key(litellm_model="m", messages=[], extra=caching.draw_extra(base, 1))
    assert k0 != k1


def test_get_llm_path_is_cached(cache_dir, monkeypatch):
    completion, calls = _completion(["first", "second"])
    monkeypatch.setattr("litellm.completion", completion)
    llm = get_llm("amazon-bedrock", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
    assert llm.invoke("p").content == "first"
    assert llm.invoke("p").content == "first"  # replayed, not re-paid
    assert len(calls) == 1
    assert llm.invoke("p", draw=1).content == "second"  # a new draw is a new sample
    assert len(calls) == 2


def test_get_llm_path_retries_throttling(monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    completion, calls = _completion([RuntimeError("ThrottlingException: rate limit"), "ok"])
    monkeypatch.setattr("litellm.completion", completion)
    assert get_llm("amazon-bedrock", "m.x").invoke("p").content == "ok"
    assert len(calls) == 2


def test_bedrock_service_unavailable_is_retried(monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    err = RuntimeError(
        'litellm.ServiceUnavailableError: BedrockException - {"message":"The system encountered an '
        'unexpected error during processing. Try your request again."}'
    )
    completion, calls = _completion([err, "ok"])
    monkeypatch.setattr("litellm.completion", completion)
    assert get_llm("amazon-bedrock", "m.x").invoke("p").content == "ok"
    assert len(calls) == 2


def test_permanent_unavailable_is_not_retried(monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    completion, calls = _completion([RuntimeError("This model is unavailable in this region"), "ok"])
    monkeypatch.setattr("litellm.completion", completion)
    with pytest.raises(RuntimeError):
        get_llm("amazon-bedrock", "m.x").invoke("p")
    assert len(calls) == 1


def test_get_llm_path_does_not_retry_real_errors(monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    completion, calls = _completion([ValueError("AccessDeniedException"), "ok"])
    monkeypatch.setattr("litellm.completion", completion)
    with pytest.raises(ValueError):
        get_llm("amazon-bedrock", "m.x").invoke("p")
    assert len(calls) == 1


def test_judge_retry_escapes_a_cached_unparseable_reply(cache_dir, monkeypatch):
    # Draw 0's first attempt returns garbage; with one cache key per request
    # the retries replayed it and the answer lost its score.
    completion, calls = _completion(["not json", GOOD, GOOD])
    monkeypatch.setattr("litellm.completion", completion)
    judge = _judge(get_llm("amazon-bedrock", "m.x"))
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(calls) == 2
    # The unparseable draw-0 reply was not stored; a re-run replays draw 1 without
    # asking draw 0 live again first.
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(calls) == 2


def _judge(llm):
    judge = Judge.__new__(Judge)
    judge._accumulate_usage = lambda usage: None
    judge.temperature = judge.seed = None
    judge._usage_lock, judge._usage_totals = threading.Lock(), {"redrawn_judgements": 0}
    judge.llm = llm
    return judge


def test_three_unparseable_replies_do_not_become_a_permanent_failure(cache_dir, monkeypatch):
    """All three attempts failing used to store all three, and every later run with
    the cache on replayed the failure without calling the model."""
    completion, calls = _completion(["not json", "still not", "nope", GOOD])
    monkeypatch.setattr("litellm.completion", completion)
    judge = _judge(get_llm("amazon-bedrock", "m.x"))
    with pytest.raises(ValueError):
        judge.score_answer(library_name="L", question="q", answer="a")
    assert len(calls) == 3
    assert not list(cache_dir.glob("*.json"))
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(calls) == 4  # a live call, not a replay


def test_llm_call_path_does_not_store_unparseable_judge_replies(cache_dir, monkeypatch):
    """OpenAI/Anthropic judges go through llm_call_with_usage, which stores too."""
    from dokimi.llm import ChatOpenAI

    completion, calls = _completion(["not json", "still not", "nope", GOOD])
    monkeypatch.setattr("litellm.completion", completion)
    judge = _judge(ChatOpenAI(model="gpt-x", api_key="k"))
    with pytest.raises(ValueError):
        judge.score_answer(library_name="L", question="q", answer="a")
    assert not list(cache_dir.glob("*.json"))
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(calls) == 4


def test_an_unparseable_entry_already_in_the_cache_is_not_replayed(cache_dir, monkeypatch):
    """Entries stored before the check existed must not keep poisoning the run."""
    completion, calls = _completion(["not json", GOOD])
    monkeypatch.setattr("litellm.completion", completion)
    llm = get_llm("amazon-bedrock", "m.x")
    assert llm.invoke("p").content == "not json"  # no cache_if: stored as before
    assert llm.invoke("p", cache_if=lambda t: t.startswith("{")).content == GOOD
    assert len(calls) == 2
    assert llm.invoke("p", cache_if=lambda t: t.startswith("{")).content == GOOD
    assert len(calls) == 2


def test_judge_draws_are_distinct_per_attempt():
    judge = _judge(MagicMock())
    judge.llm.invoke.side_effect = [MagicMock(content="not json"), MagicMock(content=GOOD)]
    judge.score_answer(library_name="L", question="q", answer="a", draw=2)
    assert [c.kwargs["draw"] for c in judge.llm.invoke.call_args_list] == [6, 7]


def _asker(llm):
    # As `commands/judge_reliability.py` builds it.
    return lambda prompt, draw=0, cache_if=None: llm.invoke(prompt, draw=draw, cache_if=cache_if).content


def test_pairwise_retries_an_unparseable_reply_and_never_caches_it(cache_dir, monkeypatch):
    """A cached "Verdict:" was replayed on every later run: the question was lost
    again with 0 live calls."""
    from dokimi.eval.pairwise import PairwiseJudge

    completion, calls = _completion(["Verdict:", "A", "B", "A"])
    monkeypatch.setattr("litellm.completion", completion)
    judge = PairwiseJudge(ask=_asker(get_llm("amazon-bedrock", "m.x")))
    assert judge.compare("L", "q", "x", "y").score == 1
    assert len(calls) == 3
    assert calls[1]["messages"] == calls[0]["messages"]  # the same prompt, a new draw
    assert len(list(cache_dir.glob("*.json"))) == 2  # "Verdict:" was not stored
    # The re-run asks draw 0 live (nothing usable stored there); the rest replays.
    assert judge.compare("L", "q", "x", "y").score == 1
    assert len(calls) == 4


def test_checklist_retries_an_unparseable_reply_and_never_caches_it(cache_dir, monkeypatch):
    from dokimi.eval import checklist as ck

    completion, calls = _completion(["Verdict:", "YES", "NO", "YES"])
    monkeypatch.setattr("litellm.completion", completion)
    ask = _asker(get_llm("amazon-bedrock", "m.x"))
    (v,) = ck.check_answer(ask, "q", "a", ["point"])
    assert (v.covered, v.absent, len(calls)) == (True, False, 3)
    assert len(list(cache_dir.glob("*.json"))) == 2
    (v,) = ck.check_answer(ask, "q", "a", ["point"])
    assert (v.covered, v.absent, len(calls)) == (True, False, 4)


def test_panel_retry_escapes_a_cached_unparseable_reply(cache_dir, monkeypatch):
    """The panel retried under one cache key, so with the cache on all three attempts
    replayed the first unparseable reply and the role's vote was lost."""
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    good = (
        '{"reasoning": "ok", "correctness": 80, "completeness": 80, "specificity": 80, '
        '"code_quality": 80, "actionability": 80}'
    )
    completion, calls = _completion(["not json", good, good])
    monkeypatch.setattr("litellm.completion", completion)
    cfg = JudgeConfig(role="technical_expert", model="gpt-x", provider="openai")
    panel = JudgePanel(judges=[cfg])
    vote = panel._call_judge(cfg, "q", "a", "L", "")
    assert (vote.error, vote.aggregate, len(calls)) == (None, 80.0, 2)
    assert calls[1]["messages"] == calls[0]["messages"]  # the same prompt, a new draw
    assert len(list(cache_dir.glob("*.json"))) == 1  # "not json" was not stored
    # The re-run replays draw 1 without asking draw 0 live again first.
    assert panel._call_judge(cfg, "q", "a", "L", "").aggregate == 80.0
    assert len(calls) == 2


def test_panel_non_numeric_dimension_is_retried_and_not_cached(cache_dir, monkeypatch):
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    bad = '{"correctness": "N/A", "completeness": 80}'
    completion, calls = _completion([bad, bad, bad])
    monkeypatch.setattr("litellm.completion", completion)
    cfg = JudgeConfig(role="doc_reviewer", model="gpt-x", provider="openai")
    vote = JudgePanel(judges=[cfg])._call_judge(cfg, "q", "a", "L", "")
    assert vote.aggregate is None and vote.error
    assert len(calls) == 3
    assert not list(cache_dir.glob("*.json"))


# ── A rerun of a judgement that needed a redraw makes no provider call ───────
# Only a parsed reply is stored, so a rerun that walked the attempts in order asked
# the unstored unparseable attempt live on every run before the stored one replayed.
# At pinned sampling the provider repeats that reply, so the leak never healed.
# The entries the first run stores are the ones every earlier version stored too.


def _pinned_judge(seed=7):
    from dokimi.llm import ChatOpenAI

    judge = _judge(ChatOpenAI(model="gpt-x", api_key="k", temperature=0.0, seed=seed))
    judge.temperature, judge.seed = 0.0, seed
    return judge


def test_judge_rerun_after_an_unparseable_reply_makes_no_provider_call(cache_dir, fake_llm):
    fake_llm.push("not json", GOOD)
    judge = _pinned_judge()
    assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(fake_llm.requests) == 2
    assert "Reminder (re-draw 1 of 2)" in fake_llm.prompts[1]
    for _ in range(2):
        assert judge.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(fake_llm.requests) == 2
    # The replayed judgement still counts as redrawn, once per judgement.
    assert judge._usage_totals["redrawn_judgements"] == 3


def test_judge_rerun_replays_the_third_attempt_without_asking_the_first_two(cache_dir, fake_llm):
    fake_llm.push("not json", "still not", GOOD)
    judge = _pinned_judge()
    judge.score_answer(library_name="L", question="q", answer="a")
    judge.score_answer(library_name="L", question="q", answer="a")
    assert len(fake_llm.requests) == 3
    assert judge._usage_totals["redrawn_judgements"] == 2


def test_judge_cache_miss_on_every_attempt_falls_through_to_live_calls(cache_dir, fake_llm):
    fake_llm.push("not json", GOOD)
    judge = _pinned_judge()
    judge.score_answer(library_name="L", question="q", answer="a")
    fake_llm.push(GOOD)
    other = _pinned_judge(seed=8)  # a different key: nothing stored for it
    assert other.score_answer(library_name="L", question="q", answer="a")["aggregate"] == 80.0
    assert len(fake_llm.requests) == 3
    assert other._usage_totals["redrawn_judgements"] == 0


def test_panel_rerun_after_an_unparseable_reply_makes_no_provider_call(cache_dir, fake_llm):
    from dokimi.eval.panel import JudgeConfig, JudgePanel

    good = (
        '{"reasoning": "ok", "correctness": 80, "completeness": 80, "specificity": 80, '
        '"code_quality": 80, "actionability": 80}'
    )
    fake_llm.push("not json", good)
    cfg = JudgeConfig(role="technical_expert", model="gpt-x", provider="openai",
                      temperature=0.0, seed=7)
    panel = JudgePanel(judges=[cfg])
    assert panel._call_judge(cfg, "q", "a", "L", "").aggregate == 80.0
    assert len(fake_llm.requests) == 2
    assert "Reminder (re-draw 1 of 2)" in fake_llm.prompts[1]
    for _ in range(2):
        vote = panel._call_judge(cfg, "q", "a", "L", "")
        assert (vote.error, vote.aggregate) == (None, 80.0)
    assert len(fake_llm.requests) == 2


def test_cache_off_skips_the_replay_probe(monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    asked = []
    assert caching.first_replayed(3, asked.append) is None
    assert asked == []


def test_replay_probe_lets_a_non_miss_error_propagate(cache_dir):
    asked = []

    def ask(attempt):
        asked.append(attempt)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        caching.first_replayed(3, ask)
    assert asked == [0]
    assert not caching.replaying_only()


def test_replay_only_raises_on_a_miss_and_sends_nothing(cache_dir, fake_llm):
    from dokimi.llm import LLM

    with caching.replay_only(), pytest.raises(caching.CacheMiss):
        LLM("gpt-x", "openai", api_key="k").complete("p")
    assert fake_llm.requests == []
    assert not caching.replaying_only()
