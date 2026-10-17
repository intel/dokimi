# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the run-telemetry layer (UsageRecord + llm.py extraction).

Covers the required guarantees: defensive token/cache/reasoning
extraction, cost-None when litellm has no pricing, additive accumulation across
turns, streaming TTFT, and the per-row metrics{} block plus the retained
token_usage alias.
"""

import pytest

import dokimi.llm as llm_mod
from dokimi.llm import _extract_usage
from dokimi.metrics.usage import UsageRecord, to_record


# ── fake litellm response shapes ────────────────────────────────────────────
class _Usage:
    def __init__(self, prompt=0, completion=0, total=0, cache_create=None,
                 cache_read=None, prompt_details=None, completion_details=None):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total
        if cache_create is not None:
            self.cache_creation_input_tokens = cache_create
        if cache_read is not None:
            self.cache_read_input_tokens = cache_read
        if prompt_details is not None:
            self.prompt_tokens_details = prompt_details
        if completion_details is not None:
            self.completion_tokens_details = completion_details


class _Resp:
    def __init__(self, usage):
        self.usage = usage


# ── extraction ──────────────────────────────────────────────────────────────
def test_extract_basic_tokens(monkeypatch):
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: 0.01)
    rec = _extract_usage(_Resp(_Usage(prompt=100, completion=40, total=140)),
                         "gpt-4o", "openai", latency_sec=1.5)
    assert (rec.prompt_tokens, rec.completion_tokens, rec.total_tokens) == (100, 40, 140)
    assert rec.cost_usd == 0.01
    assert rec.latency_sec == 1.5
    assert rec.model == "gpt-4o" and rec.provider == "openai"


def test_extract_anthropic_cache_fields(monkeypatch):
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: None)
    rec = _extract_usage(
        _Resp(_Usage(prompt=200, completion=30, total=230,
                     cache_create=150, cache_read=900)),
        "claude-sonnet-4-6", "anthropic", latency_sec=0.0)
    assert rec.cache_write_tokens == 150
    assert rec.cache_read_tokens == 900


def test_extract_openai_cached_and_reasoning_tokens(monkeypatch):
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: None)
    rec = _extract_usage(
        _Resp(_Usage(prompt=300, completion=80, total=380,
                     prompt_details={"cached_tokens": 256},
                     completion_details={"reasoning_tokens": 64})),
        "o1", "openai", latency_sec=0.0)
    assert rec.cache_read_tokens == 256
    assert rec.reasoning_tokens == 64


def test_extract_missing_usage_is_zeroed(monkeypatch):
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: None)
    rec = _extract_usage(_Resp(None), "m", "p", latency_sec=0.0)
    assert rec.total_tokens == 0 and rec.cost_usd is None


def test_cost_none_when_no_pricing(monkeypatch):
    # A model litellm has no price for makes completion_cost raise; the helper → None.
    import litellm

    from dokimi.llm import _safe_completion_cost

    def no_pricing(**kw):
        raise Exception("no pricing")

    monkeypatch.setattr(litellm, "completion_cost", no_pricing)
    assert _safe_completion_cost(_Resp(_Usage()), "made-up-model") is None


def test_zero_cost_for_a_model_missing_from_the_price_map_is_unknown(monkeypatch):
    # litellm 1.104 priced claude-sonnet-5-5 at 0.0 with no entry in its map, so a
    # run's total read as a valid $0. A 0.0 without a price entry is no price.
    import litellm

    monkeypatch.setattr(litellm, "completion_cost", lambda **kw: 0.0)
    resp = _Resp(_Usage(prompt=10, completion=5, total=15))
    rec = _extract_usage(resp, "claude-unpriced-0-0", "anthropic")
    assert rec.cost_usd is None and rec.cost_known_calls == 0
    rec = _extract_usage(resp, "claude-unpriced-0-0", "amazon-bedrock")
    assert rec.cost_usd is None and rec.cost_known_calls == 0


def test_zero_cost_for_a_model_in_the_price_map_stays_known(monkeypatch):
    # A priced model can still cost $0 (an empty completion); that is a known cost.
    import litellm

    monkeypatch.setattr(litellm, "completion_cost", lambda **kw: 0.0)
    rec = _extract_usage(_Resp(_Usage()), "gpt-4o", "openai")
    assert rec.cost_usd == 0.0 and rec.cost_known_calls == 1


# ── accumulation ─────────────────────────────────────────────────────────────
def test_add_sums_tokens_and_keeps_first_ttft():
    a = UsageRecord(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                    cost_usd=0.01, latency_sec=1.0, ttft_sec=0.3, n_calls=1)
    b = UsageRecord(prompt_tokens=20, completion_tokens=8, total_tokens=28,
                    cost_usd=0.02, latency_sec=2.0, ttft_sec=0.9, n_calls=1)
    c = a + b
    assert (c.prompt_tokens, c.completion_tokens, c.total_tokens) == (30, 13, 43)
    assert c.cost_usd == 0.03
    assert c.latency_sec == 3.0
    assert c.ttft_sec == 0.3   # first non-null wins
    assert c.n_calls == 2


def test_add_cost_none_only_when_both_none():
    none_a = UsageRecord(cost_usd=None, n_calls=1)
    priced = UsageRecord(cost_usd=0.05, n_calls=1)
    assert (none_a + none_a).cost_usd is None
    assert (none_a + priced).cost_usd == 0.05  # priced subtotal, not None
    assert (none_a + priced).as_metrics_dict()["cost_known_calls"] == 1
    assert (none_a + priced).as_metrics_dict()["n_calls"] == 2


def test_sum_builtin_works_with_zero_start():
    recs = [UsageRecord(total_tokens=5), UsageRecord(total_tokens=7)]
    assert sum(recs, 0).total_tokens == 12


# ── serialization ────────────────────────────────────────────────────────────
def test_metrics_dict_has_required_fields():
    rec = UsageRecord(prompt_tokens=12, completion_tokens=3, total_tokens=15,
                      cost_usd=0.001, latency_sec=2.0)
    m = rec.as_metrics_dict(answer_chars=740)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                "cost_usd", "latency_sec", "ttft_sec", "cache_read_tokens",
                "cache_write_tokens", "reasoning_tokens", "n_calls",
                "cost_known_calls"):
        assert key in m
    assert m["raw_answer_chars"] == 740
    assert m["final_answer_chars"] == 740  # equal until a plugin shortens


def test_token_usage_alias():
    rec = UsageRecord(prompt_tokens=12, completion_tokens=3, total_tokens=15)
    assert rec.as_token_usage_dict() == {
        "prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
    }


def test_usage_record_supports_legacy_dict_access():
    rec = UsageRecord(prompt_tokens=12, completion_tokens=3, total_tokens=15)
    assert rec["total_tokens"] == 15
    assert rec.get("prompt_tokens") == 12
    assert rec.get("missing", "fallback") == "fallback"


# ── coercion ─────────────────────────────────────────────────────────────────
def test_to_record_from_legacy_dict():
    rec = to_record({"prompt_tokens": 10, "completion_tokens": 5,
                     "total_tokens": 15}, "m", "p")
    assert isinstance(rec, UsageRecord)
    assert rec.total_tokens == 15 and rec.model == "m"


def test_to_record_passthrough_backfills_provenance():
    rec = to_record(UsageRecord(total_tokens=9), "m", "p")
    assert rec.model == "m" and rec.provider == "p" and rec.total_tokens == 9


def test_to_record_preserves_explicit_zero_n_calls():
    # An explicit n_calls=0 (a no-LLM-call row) must not silently become 1.
    assert to_record({"total_tokens": 0, "n_calls": 0}).n_calls == 0
    # Absent n_calls still defaults to 1.
    assert to_record({"total_tokens": 5}).n_calls == 1


# ── bedrock model-string routing ─────────────────────────────────────────────
def test_build_litellm_model_bedrock_openai_namespace_uses_converse_route():
    # openai.* on Bedrock 404s on the plain invoke route; needs /converse/.
    assert (
        llm_mod._build_litellm_model("us.openai.gpt-5.6-terra", "amazon-bedrock")
        == "bedrock/converse/us.openai.gpt-5.6-terra"
    )


def test_build_litellm_model_bedrock_anthropic_namespace_unaffected():
    assert (
        llm_mod._build_litellm_model(
            "us.anthropic.claude-sonnet-5", "amazon-bedrock"
        )
        == "bedrock/us.anthropic.claude-sonnet-5"
    )


# ── streaming TTFT ───────────────────────────────────────────────────────────
class _Delta:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.delta = _Delta(content)


class _Chunk:
    def __init__(self, content, usage=None):
        self.choices = [_Choice(content)]
        if usage is not None:
            self.usage = usage


def test_stream_call_measures_ttft(monkeypatch):
    import litellm

    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: None)
    chunks = [_Chunk("Hello "), _Chunk("world"),
              _Chunk(None, usage=_Usage(prompt=5, completion=2, total=7))]

    def fake_completion(**kwargs):
        assert kwargs.get("stream") is True
        return iter(chunks)

    monkeypatch.setattr(litellm, "completion", fake_completion)
    request = llm_mod.Request("m", "openai", "m", [{"role": "user", "content": "hi"}], stream=True)
    reply = llm_mod.LiteLLMAdapter()(request)
    text, rec = reply.text, reply.usage
    assert text == "Hello world"
    assert rec.ttft_sec is not None and rec.ttft_sec >= 0.0
    assert rec.total_tokens == 7  # usage read from terminal chunk


# ── caching wired into the completion path ───────────────────────────────────
class _Message:
    def __init__(self, content):
        self.content = content


class _FullChoice:
    def __init__(self, content):
        self.message = _Message(content)


class _FullResp:
    def __init__(self, content, usage):
        self.choices = [_FullChoice(content)]
        self.usage = usage


def _stub_completion(monkeypatch, calls, content="answer"):
    """Replace litellm.completion; record the kwargs each call receives."""
    import litellm

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return _FullResp(content, _Usage(prompt=9, completion=2, total=11))

    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda r, m, **kw: 0.001)


def test_cache_prefix_becomes_a_breakpoint_on_anthropic(monkeypatch):
    from dokimi import caching

    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    calls: list = []
    _stub_completion(monkeypatch, calls)

    prefix = "x" * caching.MIN_CACHEABLE_PREFIX_CHARS
    llm_mod.llm_call_with_usage(
        "**Question:** why?", model="claude-3-5-sonnet-20241022",
        provider="anthropic", api_key="k", cache_prefix=prefix,
    )

    blocks = calls[0]["messages"][0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[1]["text"] == "**Question:** why?"


def test_no_cache_prefix_keeps_the_plain_single_string_request(monkeypatch):
    """The default path must look exactly as it did before caching existed."""
    calls: list = []
    _stub_completion(monkeypatch, calls)

    llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai", api_key="k")

    assert calls[0]["messages"] == [{"role": "user", "content": "ask"}]


def test_response_cache_replays_without_calling_the_provider(monkeypatch, tmp_path):
    from dokimi import caching

    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    calls: list = []
    _stub_completion(monkeypatch, calls)

    first = llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai", api_key="k")
    second = llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai", api_key="k")

    assert len(calls) == 1               # second call served from disk
    assert second[0] == first[0]
    # Replayed usage keeps reporting what the real call cost, not $0.
    assert second[1].cost_usd == first[1].cost_usd == 0.001
    assert second[1].total_tokens == 11


def test_response_cache_distinguishes_different_prompts(monkeypatch, tmp_path):
    from dokimi import caching

    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    calls: list = []
    _stub_completion(monkeypatch, calls)

    llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai", api_key="k")
    llm_mod.llm_call_with_usage("ask?", model="gpt-6.1-sol", provider="openai", api_key="k")

    assert len(calls) == 2


# ── sampling-param plumbing ─────────────────────────────────────────────────
def test_sampling_kwargs_omits_unset_params():
    # Passing temperature=None explicitly is not the same as not passing it;
    # some providers reject a null. Unset must mean absent.
    assert llm_mod._sampling_kwargs() == {}
    assert llm_mod._sampling_kwargs(temperature=None, seed=None, max_tokens=None) == {}


def test_sampling_kwargs_includes_set_params_including_zero():
    # temperature=0 is the useful case and must survive a falsy check.
    assert llm_mod._sampling_kwargs(temperature=0.0) == {"temperature": 0.0}
    assert llm_mod._sampling_kwargs(max_tokens=100, temperature=0.5, seed=7) == {
        "max_tokens": 100, "temperature": 0.5, "seed": 7,
    }


# ── retry classification ────────────────────────────────────────────────────
def test_bedrock_service_unavailable_is_retryable():
    # Real message seen from Bedrock; it literally says to retry, but matched
    # none of the substrings, so 49 answers were dropped without one attempt.
    msg = ('litellm.ServiceUnavailableError: BedrockException - {"message":'
           '"The system encountered an unexpected error during processing. '
           'Try your request again."}')
    assert llm_mod._is_retryable(Exception(msg))


def test_bedrock_throttling_is_retryable():
    msg = ('litellm.ServiceUnavailableError: BedrockException - '
           '{"message":"Model has been throttled. Try your request again."}')
    assert llm_mod._is_retryable(Exception(msg))


def test_internal_server_error_is_retryable():
    assert llm_mod._is_retryable(Exception("litellm.InternalServerError: 500 oops"))


def test_genuine_client_errors_stay_non_retryable():
    # Retrying these just burns quota: the request itself is wrong.
    assert not llm_mod._is_retryable(Exception("AuthenticationError: invalid api key"))
    assert not llm_mod._is_retryable(
        Exception("litellm.UnsupportedParamsError: bedrock does not support "
                  "parameters: ['seed']"))
    assert not llm_mod._is_retryable(Exception("NotFoundError: model does not exist"))


@pytest.mark.parametrize("msg", [
    "ValueError: context length 1500 exceeded",
    "max_tokens 503 is above the model limit",
    "unexpected error in wrapper",
])
def test_status_digits_inside_a_permanent_error_are_not_retryable(msg):
    assert not llm_mod._is_retryable(Exception(msg))


@pytest.mark.parametrize("msg", [
    "Error code: 529",
    "HTTP 503",
    "upstream returned status 502",
])
def test_status_codes_next_to_a_status_word_are_retryable(msg):
    assert llm_mod._is_retryable(Exception(msg))


def test_streaming_call_forwards_sampling_params(monkeypatch):
    calls: list = []
    _stub_completion(monkeypatch, calls)
    import litellm

    def fake_stream(**kwargs):
        calls.append(kwargs)
        return iter([_Chunk("ok", usage=_Usage(prompt=1, completion=1, total=2))])

    monkeypatch.setattr(litellm, "completion", fake_stream)
    text, _ = llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai",
                                          api_key="k", stream=True, temperature=0.0, seed=7)
    assert text == "ok" and (calls[0]["temperature"], calls[0]["seed"]) == (0.0, 7)


def test_response_cache_still_hits_entries_written_before_sampling_params(monkeypatch, tmp_path):
    from dokimi import caching

    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    calls: list = []
    _stub_completion(monkeypatch, calls)
    old_key = caching.response_cache_key(litellm_model="gpt-6.1-sol",
                                         messages=[{"role": "user", "content": "ask"}],
                                         extra={"stream": False, "max_tokens": None})
    caching.store_response(old_key, "cached", llm_mod.UsageRecord())
    text, _ = llm_mod.llm_call_with_usage("ask", model="gpt-6.1-sol", provider="openai", api_key="k")
    assert (text, calls) == ("cached", [])
