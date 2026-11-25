# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""parse_turn: the one read of a chat response's first choice.

Lenient (the adapter's reading): no choice is an empty turn. Strict (the tool-choice probe's
reading): no choice raises. Both read the same fields the same way.
"""

from __future__ import annotations

from types import SimpleNamespace as NS

import litellm
import pytest

from dokimi import llm as llm_mod
from dokimi.commands.tools import CutBeforeToolCall, _first_turn_caller
from dokimi.llm import LiteLLMAdapter, Request, ToolCall, response_usage
from dokimi.llm_turn import ChatTurn, parse_turn


def _tc(name, id="t1"):
    return NS(id=id, function=NS(name=name, arguments="{}"))


def _resp(content=None, calls=(), finish="stop"):
    msg = NS(content=content, tool_calls=list(calls) or None)
    return NS(
        choices=[NS(message=msg, finish_reason=finish)],
        usage=NS(prompt_tokens=7, completion_tokens=3, total_tokens=10),
    )


@pytest.fixture
def no_cost(monkeypatch):
    monkeypatch.setattr(llm_mod, "_safe_completion_cost", lambda *a, **k: None)


# ── lenient ───────────────────────────────────────────────────────────────────


def test_lenient_reads_a_full_response():
    turn = parse_turn(_resp("hi", [_tc("search")], "length"))
    assert turn == ChatTurn("hi", (ToolCall("t1", "search", "{}"),), "length")
    assert turn.truncated


@pytest.mark.parametrize(
    "resp",
    [
        NS(choices=[]),
        NS(choices=None),
        NS(),
        NS(choices=[NS(message=None, finish_reason=None)]),
    ],
)
def test_lenient_reads_no_usable_choice_as_an_empty_turn(resp):
    assert parse_turn(resp) == ChatTurn(None, (), None)
    assert not parse_turn(resp).truncated


@pytest.mark.parametrize(
    "finish, truncated",
    [
        ("length", True),
        ("max_tokens", True),
        ("stop", False),
        (None, False),
    ],
)
def test_truncation_is_the_output_ceiling_finish_reasons(finish, truncated):
    assert parse_turn(_resp("x", finish=finish)).truncated is truncated


# ── strict ────────────────────────────────────────────────────────────────────


def test_strict_and_lenient_agree_on_a_full_response():
    resp = _resp("hi", [_tc("a"), _tc("b", id="t2")], "length")
    assert parse_turn(resp, strict=True) == parse_turn(resp)


@pytest.mark.parametrize(
    "resp, error",
    [
        (NS(choices=[]), IndexError),
        (NS(choices=None), TypeError),
        (NS(), AttributeError),
        (NS(choices=[NS(finish_reason="stop")]), AttributeError),  # a choice with no message
    ],
)
def test_strict_raises_where_lenient_reads_an_empty_turn(resp, error):
    assert parse_turn(resp).tool_calls == () and parse_turn(resp).text is None
    with pytest.raises(error):
        parse_turn(resp, strict=True)


def test_a_dict_shaped_tool_call_reads_its_name():
    # A tool call given as a dict, not the object an SDK returns: the old probe read its name as "".
    resp = _resp(None, [{"id": "c", "function": {"name": "read"}}], "stop")
    assert parse_turn(resp, strict=True).tool_calls == (ToolCall("c", "read", "{}"),)
    assert parse_turn(resp).tool_calls == (ToolCall("c", "read", "{}"),)


# ── the adapter reads the same turn ───────────────────────────────────────────


def test_the_adapter_reply_is_the_lenient_turn(monkeypatch, no_cost):
    resp = _resp("ok", [_tc("search")], "stop")
    monkeypatch.setattr(litellm, "completion", lambda **kw: resp)
    reply = LiteLLMAdapter()(Request("m", "openai", "m", []))
    turn = parse_turn(resp)
    assert (reply.text, reply.tool_calls, reply.finish_reason) == (
        turn.text,
        turn.tool_calls,
        turn.finish_reason,
    )
    assert reply.response is resp


def test_the_adapter_keeps_a_billed_response_without_choices(monkeypatch, no_cost):
    resp = NS(choices=[], usage=NS(prompt_tokens=100, completion_tokens=20, total_tokens=120))
    monkeypatch.setattr(litellm, "completion", lambda **kw: resp)
    reply = LiteLLMAdapter()(Request("m", "openai", "m", []))
    assert (reply.text, reply.tool_calls, reply.finish_reason) == (None, (), None)
    assert reply.usage.prompt_tokens == 100 and reply.response is resp


def test_response_usage_reads_the_raw_response(no_cost):
    assert response_usage(_resp("x"), "m", "openai", latency_sec=0.5).prompt_tokens == 7


# ── the tool-choice probe reads strictly ──────────────────────────────────────


def _caller(monkeypatch, resp, max_tokens=64):
    monkeypatch.setattr(llm_mod, "chat_completion", lambda *a, **k: resp)
    return _first_turn_caller("m", "openai", max_tokens)({"messages": [], "tools": []})


def test_probe_returns_text_names_and_truncation(monkeypatch, no_cost):
    out = _caller(monkeypatch, _resp("hello", [_tc("a"), _tc("", id="t2")], "length"))
    assert out["called"] == ["a", ""]
    assert out["text"] == "hello" and out["truncated"] is True
    assert isinstance(out["usage"], dict)


def test_probe_reads_a_missing_name_as_empty(monkeypatch, no_cost):
    out = _caller(monkeypatch, _resp(None, [_tc(None)]))
    assert out["called"] == [""]


def test_probe_reads_absent_text_as_empty(monkeypatch, no_cost):
    out = _caller(monkeypatch, _resp(None, [_tc("a")]))
    assert out["text"] == "" and out["called"] == ["a"] and out["truncated"] is False


def test_probe_cut_before_any_tool_call_is_an_item_error_with_usage(monkeypatch, no_cost):
    with pytest.raises(CutBeforeToolCall) as info:
        _caller(monkeypatch, _resp(None, [], "length"), max_tokens=16)
    assert isinstance(info.value.usage, dict) and "max_tokens=16" in str(info.value)


def test_probe_cut_after_a_tool_call_keeps_the_call(monkeypatch, no_cost):
    out = _caller(monkeypatch, _resp(None, [_tc("a")], "length"))
    assert out["called"] == ["a"] and out["truncated"] is True


def test_probe_with_no_choices_is_an_item_error_with_usage(monkeypatch, no_cost):
    with pytest.raises(IndexError) as info:
        _caller(
            monkeypatch,
            NS(choices=[], usage=NS(prompt_tokens=1, completion_tokens=1, total_tokens=2)),
        )
    assert isinstance(info.value.usage, dict)  # type: ignore[attr-defined]


def test_probe_keeps_the_name_of_a_dict_shaped_tool_call(monkeypatch, no_cost):
    out = _caller(monkeypatch, _resp(None, [{"id": "c", "function": {"name": "read"}}], "stop"))
    assert out["called"] == ["read"] and out["text"] == ""


def test_probe_measures_latency_when_the_wall_clock_steps_back(monkeypatch, no_cost):
    wall_clock = iter([100.0, 99.0])
    monotonic_clock = iter([1.0, 1.5])
    monkeypatch.setattr("dokimi.commands.tools.time", NS(time=lambda: next(wall_clock),
                                                        perf_counter=lambda: next(monotonic_clock)))
    out = _caller(monkeypatch, _resp("ok"))
    assert out["usage"]["latency_sec"] == 0.5


def test_probe_keeps_a_dict_call_with_a_malformed_function(monkeypatch, no_cost):
    out = _caller(monkeypatch, _resp(None, [{"id": "c", "function": "read"}]))
    assert out["called"] == [""]
