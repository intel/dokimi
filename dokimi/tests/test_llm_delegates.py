# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Pins for the LLM delegates that the module's own tests leave open."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from litellm import ModelResponse

from dokimi import caching, llm as llm_mod
from dokimi.llm import LLM, chat_completion, llm_call_with_usage, resolve_api_key
from dokimi.utils import get_llm


@pytest.fixture
def sent(monkeypatch):
    import litellm

    calls: list = []

    def fake(**kw):
        calls.append(kw)
        return ModelResponse(model=kw["model"], choices=[{"index": 0, "message": {
            "role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

    monkeypatch.setattr(litellm, "completion", fake)
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    return calls


@pytest.fixture
def sleeps(monkeypatch):
    slept: list = []
    monkeypatch.setattr("time.sleep", slept.append)
    return slept


def test_an_empty_tool_list_offers_no_tools(sent):
    # main's chat_completion: `if tools:`; tools=[] with tool_choice="auto" is a 400 on some providers
    chat_completion([{"role": "user", "content": "q"}], "gpt-4o-mini", "openai", tools=[], api_key="k")
    assert "tools" not in sent[0] and "tool_choice" not in sent[0]


def test_get_llm_keeps_four_attempts_with_doubling_backoff(fake_llm, sleeps, monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    error = RuntimeError("ThrottlingException: rate limit")
    fake_llm.push(error, error, error, "ok")
    assert get_llm("openai", "gpt-4o-mini", api_key="k").invoke("q").content == "ok"
    assert sleeps == [2.0, 4.0, 8.0]
    fake_llm.push(error, error, error, error)
    with pytest.raises(RuntimeError):
        get_llm("openai", "gpt-4o-mini", api_key="k").invoke("q")


def test_chat_completion_forwards_its_retry_budget(fake_llm, sleeps):
    error = RuntimeError("ThrottlingException: rate limit")
    fake_llm.push(error)
    with pytest.raises(RuntimeError):
        chat_completion([{"role": "user", "content": "q"}], "m", "openai", api_key="k", max_retries=0)
    fake_llm.push(error, "ok")
    chat_completion([{"role": "user", "content": "q"}], "m", "openai", api_key="k", retry_delay=0.5)
    assert sleeps == [0.5]


def test_a_dict_tool_call_without_arguments_reads_as_an_empty_object():
    msg = SimpleNamespace(tool_calls=[{"id": "t", "function": {"name": "f"}}])
    assert llm_mod._tool_calls(msg)[0].arguments == "{}"


def test_a_content_less_reply_stays_none_and_is_stored_as_null(monkeypatch, tmp_path):
    import litellm

    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    monkeypatch.setattr(litellm, "completion", lambda **kw: ModelResponse(
        model="gpt-4o-mini", choices=[{"index": 0, "message": {"role": "assistant", "content": None},
                                       "finish_reason": "stop"}]))
    assert llm_call_with_usage("q", "gpt-4o-mini", api_key="k")[0] is None
    assert llm_call_with_usage("q", "gpt-4o-mini", api_key="k")[0] is None  # replayed as null


def test_a_file_key_expands_the_home_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / "k").write_text(" secret\n", encoding="utf-8")
    assert resolve_api_key("openai", "file:~/k") == "secret"


def test_get_llm_prices_an_openai_reply_by_its_route(sent):
    usage = get_llm("openai", "gpt-4o-mini", api_key="k").invoke("q").usage
    assert (usage.model, usage.provider) == ("gpt-4o-mini", "litellm")
    assert usage.cost_usd is not None and usage.cost_usd > 0


def test_openai_codex_prefixes_even_a_routed_name():
    # main's _build_litellm_model checked openai-codex before the "/" pass-through
    assert LLM("openai/gpt-5", "openai-codex").litellm_model == "openai/openai/gpt-5"
