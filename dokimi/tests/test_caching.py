# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for dokimi.caching — prompt breakpoints and response reuse."""

from __future__ import annotations

from dataclasses import replace

import hashlib

import json

import pytest

from dokimi import caching
from dokimi.metrics.usage import UsageRecord

LONG = "x" * caching.MIN_CACHEABLE_PREFIX_CHARS
ANTHROPIC = {"model": "claude-3-5-sonnet-20241022", "provider": "anthropic"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Neither cache is configured unless a test says so."""
    monkeypatch.delenv(caching.PROMPT_CACHE_ENV, raising=False)
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


def _text_of(message) -> str:
    """Flatten a message's content back into the string the model sees."""
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content)


def test_no_prefix_yields_a_plain_string_message():
    messages, cached = caching.build_messages("ask", **ANTHROPIC)
    assert messages == [{"role": "user", "content": "ask"}]
    assert cached is False


def test_system_prompt_leads_the_message_list():
    messages, _ = caching.build_messages("ask", system="be terse", **ANTHROPIC)
    assert messages[0] == {"role": "system", "content": "be terse"}
    assert messages[1]["content"] == "ask"


def test_long_prefix_on_anthropic_gets_a_breakpoint():
    messages, cached = caching.build_messages(LONG + "\n", cache_prefix=None, **ANTHROPIC)
    assert cached is False  # prefix has to be passed as cache_prefix, not prompt

    messages, cached = caching.build_messages("ask", cache_prefix=LONG, **ANTHROPIC)
    assert cached is True
    blocks = messages[0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"] == LONG
    assert blocks[1]["text"] == "ask"
    assert "cache_control" not in blocks[1]


def test_cached_and_uncached_forms_are_byte_identical(monkeypatch):
    """Turning caching off may change billing, never the text the model reads."""
    on, cached_on = caching.build_messages("ask", cache_prefix=LONG, **ANTHROPIC)
    assert cached_on is True

    monkeypatch.setenv(caching.PROMPT_CACHE_ENV, "0")
    off, cached_off = caching.build_messages("ask", cache_prefix=LONG, **ANTHROPIC)

    assert cached_off is False
    assert _text_of(off[0]) == _text_of(on[0])


def test_no_separator_is_inserted_between_prefix_and_prompt():
    messages, _ = caching.build_messages("B", cache_prefix="A")
    assert messages[0]["content"] == "AB"


def test_short_prefix_skips_the_breakpoint():
    """Below the provider minimum a breakpoint only buys the write surcharge."""
    short = "x" * (caching.MIN_CACHEABLE_PREFIX_CHARS - 1)
    messages, cached = caching.build_messages("ask", cache_prefix=short, **ANTHROPIC)
    assert cached is False
    assert messages[0]["content"] == short + "ask"


def test_openai_needs_no_breakpoint():
    """OpenAI caches long prefixes itself; the field would be rejected."""
    messages, cached = caching.build_messages(
        "ask", cache_prefix=LONG, model="gpt-6.1-sol", provider="openai"
    )
    assert cached is False
    assert isinstance(messages[0]["content"], str)


@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("anthropic", "claude-3-5-sonnet-20241022", True),
        ("amazon-bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0", True),
        ("amazon-bedrock", "meta.llama3-70b-instruct-v1:0", False),
        ("google-vertex", "claude-3-5-haiku@20241022", True),
        ("google-vertex", "gemini-2.0-flash", False),
        ("bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0", True),
        ("vertex_ai", "claude-3-5-haiku@20241022", True),
        ("openrouter", "anthropic/claude-3.5-sonnet", True),
        ("openrouter", "openai/gpt-4o", False),
        ("Anthropic", "", True),
        ("openai", "gpt-6.1-sol", False),
        ("", "", False),
    ],
)
def test_supports_explicit_cache(provider, model, expected):
    assert caching.supports_explicit_cache(model, provider) is expected


@pytest.mark.parametrize("value,enabled", [("0", False), ("false", False), ("off", False),
                                           ("", False), ("1", True), ("yes", True)])
def test_prompt_cache_env_switch(monkeypatch, value, enabled):
    monkeypatch.setenv(caching.PROMPT_CACHE_ENV, value)
    assert caching.prompt_cache_enabled() is enabled


def test_prompt_cache_defaults_on():
    assert caching.prompt_cache_enabled() is True


def test_build_messages_rejects_non_string_input():
    with pytest.raises(TypeError, match="prompt must be a str"):
        caching.build_messages(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="cache_prefix must be a str"):
        caching.build_messages("ask", cache_prefix=42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Response cache
# ---------------------------------------------------------------------------


# ── Multi-turn (agentic) breakpoint ───────────────────────────────────────────


def test_long_system_prompt_in_a_conversation_gets_a_breakpoint():
    """The agent loop re-sends its skill body every turn; that is where cost is."""
    convo = [
        {"role": "system", "content": LONG},
        {"role": "user", "content": "ask"},
        {"role": "assistant", "content": "thinking"},
    ]
    out, cached = caching.annotate_system_breakpoint(convo, **ANTHROPIC)
    assert cached is True
    assert out[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert _text_of(out[0]) == LONG
    # Later turns are left alone: only the system block is a prefix of every
    # request the loop will make.
    assert out[1:] == convo[1:]
    # The caller's list is not mutated.
    assert convo[0]["content"] == LONG


@pytest.mark.parametrize(
    "convo",
    [
        [],
        [{"role": "user", "content": "x" * 40_000}],
        [{"role": "system", "content": "be terse"}, {"role": "user", "content": "ask"}],
    ],
    ids=["empty", "no-system-message", "system-below-the-floor"],
)
def test_conversations_with_nothing_worth_caching_are_returned_unchanged(convo):
    out, cached = caching.annotate_system_breakpoint(convo, **ANTHROPIC)
    assert cached is False
    assert out == convo


def test_multi_turn_breakpoint_respects_the_switch_and_the_provider(monkeypatch):
    convo = [{"role": "system", "content": LONG}, {"role": "user", "content": "ask"}]
    assert caching.annotate_system_breakpoint(convo, **ANTHROPIC)[1] is True
    # OpenAI caches long prefixes itself; annotating is not just useless there.
    assert caching.annotate_system_breakpoint(
        convo, model="gpt-4o", provider="openai"
    )[1] is False
    monkeypatch.setenv(caching.PROMPT_CACHE_ENV, "0")
    assert caching.annotate_system_breakpoint(convo, **ANTHROPIC)[1] is False


def _usage() -> UsageRecord:
    return UsageRecord(prompt_tokens=11, completion_tokens=3, total_tokens=14,
                       cost_usd=0.002, latency_sec=1.5, model="m", provider="p")


def test_response_cache_is_off_by_default():
    key = caching.response_cache_key(litellm_model="m", messages=[])
    assert caching.response_cache_dir() is None
    assert caching.load_response(key) is None
    caching.store_response(key, "text", _usage())  # must not raise or write
    assert caching.load_response(key) is None


def test_store_then_load_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    key = caching.response_cache_key(litellm_model="m", messages=[{"role": "user", "content": "q"}])

    caching.store_response(key, "answer", _usage())
    hit = caching.load_response(key)

    assert hit is not None
    text, usage = hit
    assert text == "answer"
    # Replayed as recorded: a cached run still reports what the call cost, marked as a replay.
    assert usage == replace(_usage(), replayed=True)


def test_a_replayed_hit_reports_the_recorded_cost_and_latency(monkeypatch, tmp_path):
    """The docs promise a replay, not a zero — so the numbers have to survive it.

    Reporting ``$0``/``0s`` on a hit would make cost and latency incomparable
    between a cached and an uncached run of the same pipeline. The flip side is
    that a cached run's latency is a recording; that is why the cache is opt-in.
    """
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    caching.store_response("k", "answer", _usage())

    _text, usage = caching.load_response("k")
    assert usage.latency_sec == 1.5
    assert usage.cost_usd == 0.002
    assert usage.prompt_tokens == 11


def test_key_is_insensitive_to_dict_order_but_not_to_content():
    a = caching.response_cache_key(
        litellm_model="m", messages=[{"role": "user", "content": "q"}]
    )
    b = caching.response_cache_key(
        litellm_model="m", messages=[{"content": "q", "role": "user"}]
    )
    c = caching.response_cache_key(
        litellm_model="m", messages=[{"role": "user", "content": "q!"}]
    )
    d = caching.response_cache_key(
        litellm_model="other", messages=[{"role": "user", "content": "q"}]
    )
    e = caching.response_cache_key(
        litellm_model="m", messages=[{"role": "user", "content": "q"}], extra={"stream": True}
    )
    assert a == b
    assert len({a, c, d, e}) == 4


def test_corrupt_entry_is_a_miss_not_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    key = "deadbeef"
    (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
    assert caching.load_response(key) is None


def test_entry_without_text_is_a_miss(monkeypatch, tmp_path):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    key = "nokey"
    (tmp_path / f"{key}.json").write_text(json.dumps({"usage": {}}), encoding="utf-8")
    assert caching.load_response(key) is None


@pytest.mark.parametrize(
    "payload",
    ["[]", "null", "42", '"answer"', '{"text": 7, "usage": {}}',
     '{"text": "answer", "usage": [1, 2]}', '{"text": "answer", "usage": "free"}',
     '{"text": "answer", "usage": ""}', '{"text": "answer", "usage": 0}',
     '{"text": "answer", "usage": []}', '{"text": "answer", "usage": false}',
     '{"text": "answer", "usage": {"prompt_tokens": "9"}}',
     '{"text": "answer", "usage": {"n_calls": null}}',
     '{"text": "answer", "usage": {"cost_usd": "free"}}',
     '{"text": "answer", "usage": {"latency_sec": true}}',
     '{"text": "answer", "usage": {"model": 5}}',
     '{"text": "answer", "usage": {"prompt_tokens": NaN}}'],
)
def test_entry_of_the_wrong_shape_is_a_miss_not_a_crash(monkeypatch, tmp_path, payload):
    """Valid JSON that is not a ``{"text": str, "usage": {...}}`` entry is corrupt too."""
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    (tmp_path / "shape.json").write_text(payload, encoding="utf-8")
    assert caching.load_response("shape") is None


@pytest.mark.parametrize("payload", ['{"text": "answer"}', '{"text": "answer", "usage": null}'])
def test_entry_without_usage_still_replays(monkeypatch, tmp_path, payload):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    (tmp_path / "bare.json").write_text(payload, encoding="utf-8")
    assert caching.load_response("bare") == ("answer", UsageRecord(replayed=True))


def test_usage_values_of_every_field_type_replay(monkeypatch, tmp_path):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    usage = {"prompt_tokens": 3, "total_tokens": 3.0, "cost_usd": None, "latency_sec": 1,
             "ttft_sec": 0.5, "model": "m", "n_calls": 1}
    (tmp_path / "ok.json").write_text(json.dumps({"text": "a", "usage": usage}), encoding="utf-8")
    hit = caching.load_response("ok")
    assert hit == ("a", UsageRecord(**usage, replayed=True))


def test_null_text_is_replayed_not_a_miss(monkeypatch, tmp_path):
    """A reply without content is stored as null text and must still replay."""
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    (tmp_path / "empty.json").write_text(
        json.dumps({"text": None, "usage": {}}), encoding="utf-8"
    )
    hit = caching.load_response("empty")
    assert hit is not None
    text, usage = hit
    assert text is None
    assert usage.replayed is True


def test_unknown_usage_fields_are_dropped_not_fatal(monkeypatch, tmp_path):
    """An entry written by a newer version still has a usable answer in it."""
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    key = "future"
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"text": "answer", "usage": {"prompt_tokens": 7, "warp_tokens": 1}}),
        encoding="utf-8",
    )
    hit = caching.load_response(key)
    assert hit is not None
    assert hit[0] == "answer"
    assert hit[1].prompt_tokens == 7


def test_store_survives_an_unwritable_directory(monkeypatch, tmp_path, caplog):
    """Inference is already paid for; a cache failure must not lose the answer."""
    # A file where the *parent* directory should be: mkdir raises OSError, but
    # the configured path itself does not exist, so this is a runtime failure
    # rather than the misconfiguration response_cache_dir() rejects up front.
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(blocker / "cache"))
    caching.store_response("k", "answer", _usage())
    assert "response cache" in caplog.text.lower()


def test_a_file_as_cache_dir_is_rejected_not_a_silent_permanent_miss(monkeypatch, tmp_path):
    """A cache that can never hit should say so, not look like a cold cache."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(blocker))
    with pytest.raises(ValueError, match="not a directory"):
        caching.response_cache_dir()


@pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
def test_falsey_values_disable_the_response_cache(monkeypatch, tmp_path, value):
    """Off is spelled the same way for both caches.

    Otherwise ``DOKIMI_RESPONSE_CACHE=0`` — how the prompt cache is
    disabled — enabled a cache in a directory literally named ``0``.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, value)
    assert caching.response_cache_dir() is None
    caching.store_response("k", "answer", _usage())
    assert list(tmp_path.iterdir()) == []


def test_prompt_cache_breakpoint_does_not_change_the_response_cache_key(monkeypatch):
    """The two caches are independent, and the key has to make that true.

    ``build_messages`` emits the same text as one string or as two ``text``
    blocks depending on the breakpoint decision. If that shape reached the hash,
    toggling the prompt cache would discard every paid-for response entry.
    """
    def key_with(prompt_cache: str) -> str:
        monkeypatch.setenv(caching.PROMPT_CACHE_ENV, prompt_cache)
        messages, _ = caching.build_messages(
            "question", cache_prefix=LONG, model="claude-x", provider="anthropic"
        )
        return caching.response_cache_key(litellm_model="m", messages=messages)

    assert key_with("1") == key_with("0")


def test_min_cacheable_prefix_clears_the_small_model_token_floor():
    """Anthropic's small models need 2048 tokens, not 1024.

    At ~4 chars/token a breakpoint below that floor is silently not cached while
    the write surcharge still applies — the exact waste this constant prevents.
    """
    assert caching.MIN_CACHEABLE_PREFIX_CHARS / 4 >= 2048


def test_no_temp_files_are_left_behind(monkeypatch, tmp_path):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path))
    caching.store_response("k", "answer", _usage())
    assert [p.name for p in tmp_path.iterdir()] == ["k.json"]


# ---------------------------------------------------------------------------
# Mutation pilot: what the breakpoint looks like, and what the key separates
# ---------------------------------------------------------------------------

BEDROCK_CLAUDE = {"model": "anthropic.claude-3-5-sonnet-20241022-v2:0", "provider": "bedrock"}
_EPHEMERAL = {"type": "ephemeral"}


def test_the_model_name_decides_the_prefix_breakpoint_on_a_shared_provider():
    messages, cached = caching.build_messages("q", cache_prefix=LONG, **BEDROCK_CLAUDE)
    assert cached is True
    assert messages[-1]["content"] == [
        {"type": "text", "text": LONG, "cache_control": _EPHEMERAL},
        {"type": "text", "text": "q"},
    ]


def test_the_model_name_decides_the_system_breakpoint_on_a_shared_provider():
    convo = [{"role": "system", "content": LONG}, {"role": "user", "content": "q"}]
    out, cached = caching.annotate_system_breakpoint(convo, **BEDROCK_CLAUDE)
    assert cached is True
    assert out[0] == {
        "role": "system",
        "content": [{"type": "text", "text": LONG, "cache_control": _EPHEMERAL}],
    }
    llama = {"model": "meta.llama3-70b-instruct-v1:0", "provider": "bedrock"}
    assert caching.annotate_system_breakpoint(convo, **llama) == (convo, False)


def _key(messages, extra=None):
    return caching.response_cache_key(litellm_model="m", messages=messages, extra=extra)


def test_sampling_parameters_change_the_key_and_their_order_does_not():
    user = [{"role": "user", "content": "q"}]
    assert _key(user, {"temperature": 0}) != _key(user, {"temperature": 1})
    assert _key(user, {"temperature": 0, "top_p": 1}) == _key(user, {"top_p": 1, "temperature": 0})
    assert _key(user, {}) == _key(user, None)


def test_the_role_is_part_of_the_key():
    assert _key([{"role": "system", "content": "q"}]) != _key([{"role": "user", "content": "q"}])


def test_a_text_block_list_keys_like_its_concatenation():
    blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
    assert _key([{"role": "user", "content": blocks}]) == _key([{"role": "user", "content": "ab"}])


def test_image_blocks_are_keyed_by_content_not_by_cache_control():
    def msg(url, **extra):
        image = {"type": "image_url", "image_url": {"url": url}, **extra}
        return [{"role": "user", "content": [image, {"type": "text", "text": "what is it?"}]}]

    assert _key(msg("a.png")) != _key(msg("b.png"))
    assert _key(msg("a.png")) == _key(msg("a.png", cache_control=_EPHEMERAL))


def test_the_key_of_a_known_request_does_not_change():
    """Stored entries are found by this hash: a new key format orphans every one of them.

    Change the expected value only on purpose, with a changelog entry saying the
    response cache starts cold.
    """
    key = caching.response_cache_key(
        litellm_model="anthropic/claude-x",
        messages=[
            {"role": "system", "content": "Réponds"},
            {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
        ],
        extra={"temperature": 0},
    )
    # The hashed payload, byte for byte: sorted keys, text blocks joined, UTF-8 kept.
    stored_form = (
        '{"extra": {"temperature": 0}, "messages": [{"content": "Réponds", "role": "system"}, '
        '{"content": "ab", "role": "user"}], "model": "anthropic/claude-x"}'
    )
    assert key == hashlib.sha256(stored_form.encode("utf-8")).hexdigest()


def test_a_non_json_extra_value_keys_like_its_string():
    from pathlib import PurePosixPath

    as_path = caching.response_cache_key(
        litellm_model="m", messages=[], extra={"p": PurePosixPath("/x")}
    )
    assert as_path == caching.response_cache_key(litellm_model="m", messages=[], extra={"p": "/x"})
