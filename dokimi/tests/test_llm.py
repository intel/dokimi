# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The LLM module: providers, keys, retry, draws, the response cache, usage, the seam.

Everything but the ``LiteLLMAdapter`` tests drives :class:`LLM` through the fake
adapter; those stub ``litellm.completion``. No test reaches a provider.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from dokimi import caching, llm as llm_mod
from dokimi.defaults import bedrock_profile
from dokimi.llm import (
    LLM, PROVIDERS, FakeAdapter, LiteLLMAdapter, Reply, Request, ToolCall, Unparseable,
    api_key_env, ask_parsed, chat_completion, is_retryable, llm_call, llm_call_with_usage,
    resolve_api_key, resolve_model, unsupported_sampling_params, use_adapter,
)
from dokimi.metrics.usage import UsageRecord
from dokimi.utils import get_llm


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def no_cache(monkeypatch):
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)


@pytest.fixture
def sleeps(monkeypatch):
    slept: list = []
    monkeypatch.setattr("time.sleep", slept.append)
    return slept


@pytest.fixture(autouse=True)
def _no_ambient_bedrock_env(monkeypatch):
    for var in ("ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        monkeypatch.delenv(var, raising=False)


# ── providers ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("provider, model, expected", [
    ("openai", "gpt-6.1-sol", "gpt-6.1-sol"),  # on litellm's lists: bare, so its cache key holds
    ("openai", "gpt-3.5-turbo-instruct", "gpt-3.5-turbo-instruct"),  # litellm's text-completion route
    ("openai", "glm-5.3", "openai/glm-5.3"),  # off the lists: an OPENAI_API_BASE server's model
    ("openai", "claude-sonnet-5-5", "openai/claude-sonnet-5-5"),  # bare, litellm would pick anthropic
    ("openai", "meta-llama/Llama-3.1-8B", "openai/meta-llama/Llama-3.1-8B"),  # an org, not a route
    ("openai", "openai/glm-5.3", "openai/glm-5.3"),
    ("openai", "azure/gpt-4o", "azure/gpt-4o"),  # an explicit litellm route passes through
    ("openai-codex", "gpt-5", "openai/gpt-5"),
    ("anthropic", "claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"),
    ("openrouter", "openai/gpt-oss-120b", "openrouter/openai/gpt-oss-120b"),
    ("openrouter", "openrouter/x/y", "openrouter/x/y"),
    ("google-vertex", "gemini-2.5-pro", "vertex_ai/gemini-2.5-pro"),
    ("google", "claude-3-5-haiku@20241022", "vertex_ai/claude-3-5-haiku@20241022"),
    ("gemini", "gemini-2.5-pro", "vertex_ai/gemini-2.5-pro"),
    ("amazon-bedrock", "us.anthropic.claude-haiku-4-5", "bedrock/us.anthropic.claude-haiku-4-5"),
    ("amazon-bedrock", "us.openai.gpt-oss-120b-1:0", "bedrock/converse/us.openai.gpt-oss-120b-1:0"),
    ("amazon-bedrock", "some-model", "bedrock/some-model"),  # an unknown bare name is left alone
    ("anthropic", "vertex_ai/claude", "vertex_ai/claude"),  # an explicit route passes through
    ("azure", "gpt-4o", "azure/gpt-4o"),  # off the table: the provider is the route
])
def test_model_names_resolve_to_one_litellm_route(provider, model, expected):
    assert resolve_model(model, provider) == expected
    assert LLM(model, provider).litellm_model == expected


@pytest.mark.parametrize("bare", ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"])
def test_a_known_bare_bedrock_name_runs_under_the_defaults_profile(monkeypatch, bare):
    assert resolve_model(bare, "amazon-bedrock") == f"bedrock/{bedrock_profile(bare)}"
    family = bare.split("-")[1].upper()
    monkeypatch.setenv(f"ANTHROPIC_DEFAULT_{family}_MODEL", f"eu.anthropic.{bare}")
    assert resolve_model(bare, "amazon-bedrock") == f"bedrock/eu.anthropic.{bare}"


@pytest.mark.parametrize("provider, env", [
    ("openai", "OPENAI_API_KEY"), ("openai-codex", "OPENAI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"), ("openrouter", "OPENROUTER_API_KEY"),
    ("amazon-bedrock", "AWS_BEARER_TOKEN_BEDROCK"), ("google-vertex", "GEMINI_API_KEY"),
    ("google", "GEMINI_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("azure", "OPENAI_API_KEY"),
])
def test_each_provider_reads_its_own_key(provider, env, monkeypatch, fake_llm, no_cache):
    for var in set(PROVIDERS.values()):
        monkeypatch.delenv(var, raising=False)
    assert api_key_env(provider) == env
    monkeypatch.setenv(env, "from-env")
    fake_llm.push("a", "b")
    LLM("m.x", provider).complete("p")
    LLM("m.x", provider, api_key="explicit").complete("p")
    assert [r.api_key for r in fake_llm.requests] == ["from-env", "explicit"]


def test_no_key_is_sent_as_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_api_key("openai") is None


def test_a_file_key_is_read(tmp_path, fake_llm, no_cache):
    key_file = tmp_path / "key"
    key_file.write_text("secret\n", encoding="utf-8")
    fake_llm.push("ok")
    LLM("m", "openai", api_key=f"file:{key_file}").complete("p")
    assert fake_llm.requests[0].api_key == "secret"


def test_an_unreadable_file_key_raises_even_on_a_cache_hit(tmp_path, cache_dir, fake_llm):
    fake_llm.push("stored")
    LLM("m", "openai", api_key="k").complete("p")
    with pytest.raises(ValueError, match="Cannot read API key"):
        LLM("m", "openai", api_key=f"file:{tmp_path / 'missing'}").complete("p")


# ── retry ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("error", [
    RuntimeError("ThrottlingException: rate limit"),
    RuntimeError('litellm.ServiceUnavailableError: BedrockException - {"message":"The system '
                 'encountered an unexpected error during processing. Try your request again."}'),
    RuntimeError("Error code: 529 overloaded"),
])
def test_transient_errors_are_retried_with_doubling_backoff(error, fake_llm, no_cache, sleeps):
    fake_llm.push(error, error, "ok")
    assert LLM("m.x", "amazon-bedrock").ask("p") == "ok"
    assert len(fake_llm.requests) == 3
    assert sleeps == [2.0, 4.0]


def test_the_backoff_doubles_from_retry_delay(fake_llm, no_cache, sleeps):
    error = RuntimeError("ThrottlingException: rate limit")
    fake_llm.push(error, error, error, "ok")
    assert LLM("m.x", "amazon-bedrock", retry_delay=0.5, max_retries=3).ask("p") == "ok"
    assert sleeps == [0.5, 1.0, 2.0]  # doubling, not adding


@pytest.mark.parametrize("error", [
    ValueError("AccessDeniedException"),
    RuntimeError("This model is unavailable in this region"),
    RuntimeError("AuthenticationError: invalid api key"),
])
def test_permanent_errors_are_raised_on_the_first_attempt(error, fake_llm, no_cache, sleeps):
    fake_llm.push(error, "ok")
    with pytest.raises(type(error)):
        LLM("m.x", "amazon-bedrock").ask("p")
    assert len(fake_llm.requests) == 1 and sleeps == []


def test_retries_are_bounded(fake_llm, no_cache, sleeps):
    fake_llm.push(*[RuntimeError("rate limit")] * 5)
    with pytest.raises(RuntimeError):
        LLM("m", "openai", max_retries=2).ask("p")
    assert len(fake_llm.requests) == 3


def test_the_exception_type_outranks_its_message():
    class AuthenticationError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    assert not is_retryable(AuthenticationError("connection to https://x failed"))
    assert is_retryable(RateLimitError("nothing transient-sounding here"))


# ── sampling and request shape ──────────────────────────────────────────────


def test_sampling_is_sent_only_when_set(fake_llm, no_cache):
    fake_llm.push("a", "b", "c")
    LLM("m", "openai").complete("p")
    LLM("m", "openai", temperature=0.0, seed=7).complete("p")
    LLM("m", "openai", temperature=0.2).chat([{"role": "user", "content": "p"}])
    assert [(r.temperature, r.seed) for r in fake_llm.requests] == [(None, None), (0.0, 7), (0.2, None)]


def test_max_tokens_is_the_objects_unless_a_call_overrides_it(fake_llm, no_cache):
    fake_llm.push("a", "b", "c", "d")
    LLM("m", "openrouter").complete("p")
    LLM("m", "openrouter", max_tokens=500).complete("p")
    LLM("m", "openrouter", max_tokens=500).complete("p", max_tokens=50)
    LLM("m", "openrouter", max_tokens=500).chat([{"role": "user", "content": "p"}], max_tokens=60)
    assert [r.max_tokens for r in fake_llm.requests] == [None, 500, 50, 60]


def test_a_plain_prompt_is_one_user_message(fake_llm, no_cache):
    fake_llm.push("ok")
    LLM("gpt-6.1-sol", "openai").complete("ask")
    assert fake_llm.requests[0].messages == [{"role": "user", "content": "ask"}]


def test_system_and_cache_prefix_shape_the_request(fake_llm, no_cache):
    prefix = "x" * caching.MIN_CACHEABLE_PREFIX_CHARS
    fake_llm.push("ok")
    LLM("claude-sonnet-5-5", "anthropic").complete("**Q:** why?", system="sys", cache_prefix=prefix)
    system, user = fake_llm.requests[0].messages
    assert system == {"role": "system", "content": "sys"}
    assert user["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert fake_llm.prompts == [prefix + "**Q:** why?"]


def test_unsupported_sampling_params_check_the_resolved_route(monkeypatch):
    import litellm

    seen = []

    def supported(model):
        seen.append(model)
        return ["temperature"]

    monkeypatch.setattr(litellm, "get_supported_openai_params", supported)
    assert unsupported_sampling_params("claude-sonnet-5-5", "amazon-bedrock",
                                       temperature=0.0, seed=1) == ["seed"]
    assert seen == [f"bedrock/{bedrock_profile('claude-sonnet-5-5')}"]
    assert unsupported_sampling_params("m", "openai") == []  # nothing requested, nothing checked


def test_unsupported_sampling_params_never_block_when_the_check_fails(monkeypatch):
    import litellm

    def broken(model):
        raise RuntimeError("no such model")

    monkeypatch.setattr(litellm, "get_supported_openai_params", broken)
    assert unsupported_sampling_params("m", "openai", seed=1) == []
    monkeypatch.setattr(litellm, "get_supported_openai_params", lambda model: [])
    assert unsupported_sampling_params("m", "openai", seed=1) == []


# ── the response cache ──────────────────────────────────────────────────────

PREFIX = "p" * caching.MIN_CACHEABLE_PREFIX_CHARS

# File names main's ``llm_call_with_usage`` and ``utils.get_llm`` wrote for these calls,
# recorded by running that code against a stubbed ``litellm.completion``.
MAIN_WRITTEN = [
    ("3e7199be1714ff54b3d8e70f9999496954ca9bed4f428c63e0bdb2eafc318590",
     lambda: LLM("gpt-6.1-sol", "openai", api_key="k").complete("ask")),
    ("6db34004f536559c2fde341f094f89573dbeacd3add4943113f87f3a9bd3729c",
     lambda: LLM("claude-sonnet-5-5", "anthropic", api_key="k").complete(
         "ask", system="sys", cache_prefix=PREFIX)),
    ("c8a2f261f7a9ffb976d8a9729ffef7fa356c853101dd4813f3c70d7d26413edf",
     lambda: LLM("claude-sonnet-5-5", "amazon-bedrock", api_key="k", temperature=0.0, seed=7,
                 max_tokens=500).complete("ask", draw=2)),
    ("79a2f845dc1f573efb5fc17ff243719c5918a4b318624064699797c8378e9d6c",
     lambda: LLM("gpt-6.1-sol", "openai", api_key="k").complete("ask", stream=True)),
    ("4c8f44a8bb0344dec5b8471f8c6ec99c2bbf785f5dd323fc2b99c48f0275af9c",
     lambda: get_llm("amazon-bedrock", "claude-sonnet-5-5", "k").invoke("ask")),
    ("22510fd74bf01210757177f302f9b6728b81e9e3b31572bd6245f0c88af78dfd",
     lambda: get_llm("openrouter", "openai/gpt-oss-120b", "k", temperature=0.0).invoke(
         "ask", draw=1)),
]


@pytest.mark.parametrize("key, call", MAIN_WRITTEN,
                         ids=["plain", "system+prefix", "pinned+max_tokens+draw", "stream",
                              "get_llm-bedrock", "get_llm-openrouter"])
def test_an_entry_main_wrote_is_hit(key, call, cache_dir, fake_llm):
    usage = UsageRecord(total_tokens=7, cost_usd=0.002, cost_known_calls=1)
    (cache_dir / f"{key}.json").write_text(
        json.dumps({"text": "written by main", "usage": asdict(usage)}), encoding="utf-8")
    reply = call()
    text = reply.text if isinstance(reply, Reply) else reply.content
    assert text == "written by main"
    assert reply.usage.cost_usd == 0.002 and reply.usage.replayed
    assert fake_llm.requests == []


def test_a_cached_reply_is_replayed_with_its_stored_usage(cache_dir):
    priced = UsageRecord(prompt_tokens=9, completion_tokens=2, total_tokens=11, cost_usd=0.001,
                         cost_known_calls=1)
    fake = FakeAdapter("first", "second", usage=priced)
    llm = LLM("gpt-6.1-sol", "openai", adapter=fake)
    first, second = llm.complete("ask"), llm.complete("ask")
    assert len(fake.requests) == 1
    assert second.text == first.text == "first"
    assert second.usage.cost_usd == 0.001  # what the real call cost, not $0


def test_sampling_and_max_tokens_key_their_own_entries(cache_dir, fake_llm):
    fake_llm.push("free", "pinned", "seeded", "capped")
    assert LLM("m", "openai").ask("p") == "free"
    assert LLM("m", "openai", temperature=0.0).ask("p") == "pinned"
    assert LLM("m", "openai", temperature=0.0, seed=1).ask("p") == "seeded"
    assert LLM("m", "openai", max_tokens=10).ask("p") == "capped"
    assert LLM("m", "openai").ask("p") == "free"
    assert LLM("m", "openai", temperature=0.0).ask("p") == "pinned"
    assert len(fake_llm.requests) == 4


def test_a_new_draw_is_a_new_sample(cache_dir, fake_llm):
    fake_llm.push("first", "second")
    llm = LLM("m.x", "amazon-bedrock")
    assert llm.ask("p") == llm.ask("p") == "first"
    assert llm.ask("p", draw=1) == "second"
    assert len(fake_llm.requests) == 2


def test_cache_if_guards_both_storing_and_replaying(cache_dir, fake_llm):
    fake_llm.push("not json", "{}")
    llm = LLM("m.x", "amazon-bedrock")
    assert llm.ask("p") == "not json"  # no cache_if: stored
    is_json = lambda t: t.startswith("{")  # noqa: E731
    assert llm.ask("p", cache_if=is_json) == "{}"  # the stored reply is not replayed
    assert llm.ask("p", cache_if=is_json) == "{}"
    assert len(fake_llm.requests) == 2


def test_a_reply_without_content_is_stored_and_replayed_as_before(cache_dir, fake_llm):
    fake_llm.push(Reply(text=None, usage=UsageRecord()))
    llm = LLM("m", "openai")
    assert llm.complete("p").text is None
    assert llm.complete("p").text is None
    assert llm.ask("p") == ""
    assert len(fake_llm.requests) == 1


def test_chat_is_never_response_cached(cache_dir, fake_llm):
    fake_llm.push("a", "b")
    llm = LLM("m", "openai")
    msgs = [{"role": "user", "content": "q"}]
    assert [llm.chat(msgs).text, llm.chat(msgs).text] == ["a", "b"]
    assert not list(cache_dir.glob("*.json"))


def test_chat_puts_a_breakpoint_on_a_long_system_prompt(fake_llm):
    fake_llm.push("ok")
    system = {"role": "system", "content": "s" * caching.MIN_CACHEABLE_PREFIX_CHARS}
    LLM("claude-sonnet-5-5", "anthropic").chat([system, {"role": "user", "content": "q"}])
    assert fake_llm.requests[0].messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


# ── truncation and usage ────────────────────────────────────────────────────


@pytest.mark.parametrize("finish_reason, truncated", [
    ("length", True), ("max_tokens", True), ("stop", False), (None, False),
])
def test_a_reply_knows_when_it_was_cut(finish_reason, truncated):
    assert Reply("t", UsageRecord(), finish_reason=finish_reason).truncated is truncated


def test_a_live_cut_reply_is_logged_and_its_replay_is_not(cache_dir, fake_llm, caplog):
    fake_llm.push(Reply("[{", UsageRecord(completion_tokens=4096), finish_reason="length"))
    llm = LLM("m", "openai", max_tokens=4096)
    with caplog.at_level(logging.WARNING, logger=llm_mod.__name__):
        llm.complete("p")
        llm.complete("p")
    cut = [r.getMessage() for r in caplog.records if "output ceiling" in r.getMessage()]
    assert cut == ["LLM reply hit the output ceiling (m, max_tokens=4096, 4096 tokens); "
                   "raise max_tokens if this repeats"]


def test_metrics_total_every_reply_and_stay_null_when_unpriced(cache_dir):
    llm = LLM("m", "openai", adapter=FakeAdapter("a", "b"))
    llm.ask("p")
    llm.ask("p")  # a replay counts: it reports the modelled call
    llm.ask("q")
    total = llm.metrics()
    assert (total.n_calls, total.total_tokens, total.cost_usd) == (3, 45, None)
    priced = LLM("m", "openai", adapter=FakeAdapter(
        "a", usage=UsageRecord(total_tokens=1, cost_usd=0.25, cost_known_calls=1)))
    priced.ask("a prompt not in the cache")
    assert (priced.metrics().cost_usd, priced.metrics().cost_known_calls) == (0.25, 1)


# ── the old entry points delegate ───────────────────────────────────────────


def test_llm_call_sends_what_llm_complete_sends(fake_llm, no_cache):
    fake_llm.push("one", "two", "three")
    kwargs = dict(system="sys", cache_prefix="pre ", temperature=0.0, seed=3)
    LLM("claude-sonnet-5-5", "anthropic", api_key="k", temperature=0.0, seed=3).complete(
        "q", system="sys", cache_prefix="pre ")
    text, usage = llm_call_with_usage("q", "claude-sonnet-5-5", "anthropic", api_key="k", **kwargs)
    assert llm_call("q", "claude-sonnet-5-5", "anthropic", api_key="k", **kwargs) == "three"
    first, second, third = fake_llm.requests
    assert first == second == third
    assert (text, usage.model, usage.provider) == ("two", "claude-sonnet-5-5", "anthropic")


def test_the_legacy_calls_forward_retries_max_tokens_and_stream(fake_llm, no_cache, sleeps):
    error = RuntimeError("ThrottlingException: rate limit")
    fake_llm.push(error, error, "ok")
    text, _ = llm_call_with_usage("q", "m.x", "amazon-bedrock", max_retries=2, retry_delay=0.25,
                                  max_tokens=77, stream=True)
    assert text == "ok" and sleeps == [0.25, 0.5]
    assert {(r.max_tokens, r.stream) for r in fake_llm.requests} == {(77, True)}
    fake_llm.push(error)
    with pytest.raises(RuntimeError, match="Throttling"):
        llm_call("q", "m.x", "amazon-bedrock", max_retries=0)
    assert sleeps == [0.25, 0.5]  # max_retries=0: no retry, no sleep


def test_draw_is_forwarded_by_the_legacy_calls_and_the_shims(fake_llm, cache_dir):
    fake_llm.push("d0", "d1", "c1")
    shim = llm_mod.ChatOpenAI("gpt-6.1-sol", api_key="k")
    assert shim.invoke("q").content == "d0"
    assert shim.invoke("q", draw=1).content == "d1"  # its own sample, not draw 0's entry
    assert shim.invoke("q", draw=1).content == "d1"  # and replayed as such
    assert llm_call("q", "gpt-6.1-sol", "openai", api_key="k", draw=1) == "d1"
    assert llm_call("q", "gpt-6.1-sol", "openai", api_key="k", draw=2) == "c1"
    assert len(fake_llm.requests) == 3


def test_get_llm_reads_an_explicit_file_key(tmp_path, fake_llm, no_cache):
    key_file = tmp_path / "key"
    key_file.write_text("secret\n", encoding="utf-8")
    fake_llm.push("ok")
    get_llm("openai", "gpt-6.1-sol", api_key=f"file:{key_file}").invoke("q")
    assert fake_llm.requests[0].api_key == "secret"


def test_the_chat_shims_delegate_with_their_provider(fake_llm, no_cache):
    fake_llm.push("a", "b")
    assert llm_mod.ChatOpenAI("gpt-6.1-sol", api_key="k", temperature=0.0).invoke("q").content == "a"
    assert llm_mod.ChatAnthropic("claude-sonnet-5-5", api_key="k").invoke("q").content == "b"
    assert [(r.litellm_model, r.temperature) for r in fake_llm.requests] == [
        ("gpt-6.1-sol", 0.0), ("anthropic/claude-sonnet-5-5", None)]


def test_chat_completion_is_llm_chat_returning_the_raw_response(fake_llm):
    raw = object()
    fake_llm.push(Reply(None, UsageRecord(), response=raw))
    tools = [{"type": "function", "function": {"name": "search"}}]
    out = chat_completion([{"role": "user", "content": "q"}], model="m", provider="openai",
                          tools=tools, tool_choice="required", api_key="k", max_tokens=0, seed=1)
    assert out is raw
    (req,) = fake_llm.requests
    assert (req.tools, req.tool_choice, req.max_tokens, req.seed, req.api_key) == (
        tools, "required", 0, 1, "k")


@pytest.mark.parametrize("provider, model, route, env, max_tokens", [
    ("openrouter", "x/y", "openrouter/x/y", "OPENROUTER_API_KEY", 2000),
    ("google", "gemini-2.5-pro", "vertex_ai/gemini-2.5-pro", "GEMINI_API_KEY", None),
    ("anthropic", "claude-sonnet-5-5", "anthropic/claude-sonnet-5-5", "ANTHROPIC_API_KEY", None),
    ("openai", "gpt-6.1-sol", "gpt-6.1-sol", "OPENAI_API_KEY", None),
    ("amazon-bedrock", "m.x", "bedrock/m.x", "AWS_BEARER_TOKEN_BEDROCK", None),
])
def test_get_llm_keeps_its_own_routes_keys_and_labels(provider, model, route, env, max_tokens,
                                                      monkeypatch, fake_llm, no_cache):
    monkeypatch.setenv(env, "from-env")
    fake_llm.push("ok")
    resp = get_llm(provider, model).invoke("q")
    (req,) = fake_llm.requests
    assert (req.litellm_model, req.api_key, req.max_tokens) == (route, "from-env", max_tokens)
    assert (resp.content, resp.usage.model, resp.usage.provider) == ("ok", route, "litellm")


def test_get_llm_on_bedrock_reads_only_the_bearer_token(monkeypatch, fake_llm, no_cache):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.sonnet")
    fake_llm.push("ok", "ok")
    get_llm("amazon-bedrock", "claude-sonnet-4-5", api_key="ignored").invoke("q")
    get_llm("azure", "gpt-4o", api_key="explicit").invoke("q")
    assert [(r.litellm_model, r.api_key) for r in fake_llm.requests] == [
        ("bedrock/us.anthropic.sonnet", ""), ("azure/gpt-4o", "explicit")]


# ── the seam ────────────────────────────────────────────────────────────────


def test_an_unscripted_fake_call_fails_the_test():
    with pytest.raises(AssertionError, match="unscripted"):
        LLM("m", "openai", adapter=FakeAdapter()).chat([{"role": "user", "content": "q"}])


def test_the_fake_answers_from_respond_once_the_script_is_empty():
    fake = FakeAdapter("scripted", respond=lambda r: f"echo {llm_mod.prompt_text(r)}")
    llm = LLM("m", "openai", adapter=fake)
    msgs = [{"role": "user", "content": "q"}]
    assert [llm.chat(msgs).text, llm.chat(msgs).text] == ["scripted", "echo q"]
    assert llm_mod.prompt_text(Request("m", "p", "m", [])) == ""


def test_use_adapter_restores_the_previous_default(fake_llm):
    inner = FakeAdapter("inner")
    with use_adapter(inner):
        assert LLM("m", "openai").chat([{"role": "user", "content": "q"}]).text == "inner"
    fake_llm.push("outer")
    assert LLM("m", "openai").chat([{"role": "user", "content": "q"}]).text == "outer"


def _litellm_response(content, finish_reason="stop", tool_calls=None, usage=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
                           usage=usage)


def test_the_litellm_adapter_sends_one_completion(monkeypatch):
    import litellm

    sent = []
    resp = _litellm_response("hi", finish_reason="length",
                             usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2,
                                                   total_tokens=5))
    monkeypatch.setattr(litellm, "completion", lambda **kw: sent.append(kw) or resp)
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: 0.01)
    reply = LiteLLMAdapter()(Request("m", "openai", "m", [{"role": "user", "content": "q"}],
                                     api_key="k", max_tokens=0, temperature=0.0))
    assert sent == [{"model": "m", "messages": [{"role": "user", "content": "q"}],
                     "api_key": "k", "max_tokens": 0, "temperature": 0.0}]
    assert (reply.text, reply.truncated, reply.response) == ("hi", True, resp)
    assert (reply.usage.total_tokens, reply.usage.cost_usd, reply.usage.model) == (5, 0.01, "m")


def test_the_litellm_adapter_offers_tools_and_reads_tool_calls(monkeypatch):
    import litellm

    sent = []
    calls = [{"id": "c1", "function": {"name": "search", "arguments": '{"q": "x"}'}},
             SimpleNamespace(id="c2", function=SimpleNamespace(name="read", arguments=None))]
    monkeypatch.setattr(litellm, "completion",
                        lambda **kw: sent.append(kw) or _litellm_response(None, tool_calls=calls))
    tools = [{"type": "function", "function": {"name": "search"}}]
    reply = LiteLLMAdapter()(Request("m", "openai", "m", [], tools=tools))
    assert sent[0]["tools"] == tools and sent[0]["tool_choice"] == "auto"
    assert reply.tool_calls == (ToolCall("c1", "search", '{"q": "x"}'), ToolCall("c2", "read", "{}"))


def test_a_billed_reply_without_choices_keeps_its_usage(monkeypatch, cache_dir, sleeps):
    import litellm

    from dokimi.commands.tools import _first_turn_caller

    resp = SimpleNamespace(choices=[], usage=SimpleNamespace(
        prompt_tokens=100, completion_tokens=20, total_tokens=120))
    monkeypatch.setattr(litellm, "completion", lambda **kw: resp)
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: 0.01)
    # chat_completion hands the raw response on, so the tool probe still prices it
    assert chat_completion([{"role": "user", "content": "q"}], "m") is resp
    with pytest.raises(IndexError) as info:
        _first_turn_caller("m", "openai", 64)({"messages": [], "tools": []})
    assert info.value.usage["cost_usd"] == 0.01
    with pytest.raises(IndexError):
        llm_call("q", "m", max_retries=0)
    assert not list(cache_dir.glob("*.json")) and sleeps == []


def test_the_litellm_adapter_streams_and_measures_ttft(monkeypatch):
    import litellm

    def chunk(content, finish_reason=None, usage=None):
        delta = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
                               usage=usage)

    sent = []
    chunks = [chunk("Hello "), chunk("world", finish_reason="stop"),
              chunk(None, usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2,
                                                total_tokens=7))]
    monkeypatch.setattr(litellm, "completion", lambda **kw: sent.append(kw) or iter(chunks))
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: None)
    reply = LiteLLMAdapter()(Request("m", "openai", "m", [], max_tokens=0, stream=True))
    assert "max_tokens" not in sent[0] and sent[0]["stream_options"] == {"include_usage": True}
    assert (reply.text, reply.finish_reason, reply.usage.total_tokens) == ("Hello world", "stop", 7)
    assert reply.usage.ttft_sec is not None


def test_a_streamed_llm_call_sends_its_key_and_sampling_and_stamps_the_first_chunk(monkeypatch, no_cache):
    import litellm

    def chunk(content, usage=None):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content),
                                                        finish_reason=None)], usage=usage)

    ticks = iter(range(100))
    monkeypatch.setattr(llm_mod.time, "time", lambda: float(next(ticks)))
    sent = []
    chunks = [chunk("a"), chunk("b"), chunk("c"),
              chunk(None, usage=SimpleNamespace(prompt_tokens=1, completion_tokens=3, total_tokens=4))]
    monkeypatch.setattr(litellm, "completion", lambda **kw: sent.append(kw) or iter(chunks))
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: None)
    llm = LLM("m", "openai", api_key="k", temperature=0.0, seed=0, adapter=LiteLLMAdapter())
    reply = llm.complete("q", stream=True)
    assert reply.text == "abc"
    assert (sent[0]["api_key"], sent[0]["temperature"], sent[0]["seed"]) == ("k", 0.0, 0)
    assert reply.usage.ttft_sec == 1.0  # one tick after the request: the first chunk, not the last


# ── ask_parsed ──────────────────────────────────────────────────────────────


def _ask(replies):
    calls = []

    def ask(prompt, draw=0, cache_if=None):
        calls.append((draw, cache_if))
        return replies[len(calls) - 1]

    return ask, calls


def test_ask_parsed_redraws_until_a_reply_parses():
    ask, calls = _ask(["junk", "42"])
    value, raw = ask_parsed(ask, "p", lambda t: int(t) if t.isdigit() else None)
    assert (value, raw) == (42, "42")
    assert [d for d, _ in calls] == [0, 1]


def test_ask_parsed_draws_are_distinct_per_base_draw():
    ask, calls = _ask(["x", "x", "1"])
    ask_parsed(ask, "p", lambda t: int(t) if t.isdigit() else None, draw=2)
    assert [d for d, _ in calls] == [6, 7, 8]


def test_ask_parsed_caches_only_what_parses():
    ask, calls = _ask(["1"])
    ask_parsed(ask, "p", int)
    cache_if = calls[0][1]
    assert cache_if("7") is True
    assert cache_if("seven") is False  # parse raised


def test_ask_parsed_raises_the_last_parse_error_with_the_last_reply():
    def parse(t):
        raise ValueError(f"bad {t}")

    ask, _ = _ask(["a", "b", "c"])
    with pytest.raises(Unparseable, match="bad c") as exc:
        ask_parsed(ask, "p", parse)
    assert exc.value.reply == "c"
    assert isinstance(exc.value, ValueError)


def test_ask_parsed_names_a_reply_parse_rejected():
    ask, _ = _ask(["a", "b", "c"])
    with pytest.raises(Unparseable, match="no parseable reply, last 'c'"):
        ask_parsed(ask, "p", lambda t: None)


def test_ask_parsed_reprompts_only_the_redraws():
    asked = []

    def ask(prompt, draw=0, cache_if=None):
        asked.append(prompt)
        return "bad" if len(asked) < 3 else "1"

    value, _ = ask_parsed(ask, "p", lambda r: int(r), reprompt=lambda p, i: f"{p}+{i}")
    assert value == 1 and asked == ["p", "p+1", "p+2"]


def test_ask_parsed_propagates_call_errors():
    def ask(prompt, draw=0, cache_if=None):
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        ask_parsed(ask, "p", int)


def test_unparseable_replies_are_never_cached_end_to_end(cache_dir, fake_llm):
    """Three unparseable attempts must not be stored, or every later run replays the failure."""
    fake_llm.push("not json", "still not", "nope", "{}")
    llm = LLM("m.x", "amazon-bedrock")
    with pytest.raises(Unparseable):
        ask_parsed(llm.ask, "p", lambda t: t if t.startswith("{") else None)
    assert not list(cache_dir.glob("*.json"))
    assert ask_parsed(llm.ask, "p", lambda t: t if t.startswith("{") else None)[0] == "{}"
    assert len(fake_llm.requests) == 4  # a live call, not a replay
