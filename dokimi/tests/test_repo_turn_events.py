# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``turn`` and ``compaction`` events of ``events.jsonl`` (docs/events-format.md).

The acceptance rule: the input and cache counts of a complete Claude trace's turns sum to the run's own
totals, in the scope the totals were taken in (tree with subagents, or the parent's alone).
"""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.artifacts import schema_errors
from dokimi.context.consistency import run_from_events
from dokimi.repos.event_format import TOOL_KINDS
from dokimi.repos.events import convert
from dokimi.repos.process_metrics import process_metrics

TURN_FIELDS = ("input", "cache_read", "cache_write")


def _lines(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _usage(inp: int, out: int, read: int, create: int) -> dict:
    return {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
            "cache_creation_input_tokens": create}


def _call(msg_id: str, usage: dict, stamp: str, *, blocks: list | None = None, parent: str | None = None) -> dict:
    record = {"type": "assistant", "timestamp": stamp,
              "message": {"id": msg_id, "model": "m", "content": blocks or [], "usage": usage}}
    if parent is not None:
        record["parent_tool_use_id"] = parent
    return record


def _claude_run(*, tree: bool) -> str:
    """Three model calls: the parent's first call split over two records (one message id, the same usage on
    both), a subagent's call, then the parent's second call; a compaction after the first call. The result's
    output count is the billed one, larger than the records' sum, so the remainder carries the difference."""
    main_a = _usage(3, 5, 100, 0)
    sub = _usage(2, 4, 50, 10)
    main_b = _usage(1, 9, 120, 20)
    if tree:  # the result's modelUsage: the scope the totals sum over subagents
        totals = {"inputTokens": 6, "outputTokens": 600, "cacheReadInputTokens": 270, "cacheCreationInputTokens": 30}
        result_usage: dict = {"modelUsage": {"m": totals}}
    else:  # the parent's usage alone
        result_usage = {"usage": {"input_tokens": 4, "output_tokens": 400, "cache_read_input_tokens": 220,
                                  "cache_creation_input_tokens": 20}}
    return _lines(
        {"type": "system", "subtype": "init", "model": "m", "claude_code_version": "2.0.0", "tools": []},
        _call("a", main_a, "2026-11-14T10:00:00Z", blocks=[{"type": "text", "text": "thinking"}]),
        _call("a", main_a, "2026-11-14T10:00:00.500Z",
              blocks=[{"type": "tool_use", "id": "agent-1", "name": "Agent", "input": {"prompt": "x"}}]),
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-14T10:00:03Z",
         "compact_metadata": {"trigger": "auto", "pre_tokens": 150000}},
        _call("s", sub, "2026-11-14T10:00:02Z", parent="agent-1"),
        _call("b", main_b, "2026-11-14T10:00:05Z"),
        {"type": "result", "result": "done", "num_turns": 2, "is_error": False, "subtype": "success",
         **result_usage},
    )


def _turns(events: list[dict]) -> list[dict]:
    return [e for e in events if e["kind"] == "turn"]


def test_the_turns_of_a_tree_scope_run_sum_to_its_totals():
    transcript = convert("claude-stream-json", _claude_run(tree=True))
    turns = _turns(transcript.events)
    assert transcript.usage_scope == "tree"
    assert [sum(t[f] for t in turns) for f in TURN_FIELDS] == [
        transcript.input_tokens, transcript.cache_read_tokens, transcript.cache_write_tokens]
    assert [sum(t["output"] for t in turns), transcript.output_tokens] == [18, 600]


def test_the_turns_of_a_main_scope_run_sum_to_the_parents_totals():
    transcript = convert("claude-stream-json", _claude_run(tree=False))
    parent = [t for t in _turns(transcript.events) if "agent" not in t]
    assert transcript.usage_scope == "main"
    assert [sum(t[f] for t in parent) for f in TURN_FIELDS] == [
        transcript.input_tokens, transcript.cache_read_tokens, transcript.cache_write_tokens]
    assert [t["agent"] for t in _turns(transcript.events) if "agent" in t] == ["agent-1"]


def test_one_turn_per_model_call_with_its_prompt_and_its_time():
    events = convert("claude-stream-json", _claude_run(tree=True)).events
    turns = _turns(events)
    assert [t["turn"] for t in turns] == [0, 1, 2]  # the subagent's call counts; the split call is one call
    assert [t["output"] for t in turns] == [5, 4, 9]  # the split call's records share one count, not a sum
    assert [t["prompt_tokens"] for t in turns] == [103, 62, 141]  # input + cache_read + cache_write
    assert [t["t"] for t in turns] == [0.0, 2.0, 5.0]  # seconds from the first timestamped line


def test_the_remainder_closes_the_turns_against_the_totals():
    events = convert("claude-stream-json", _claude_run(tree=True)).events
    turns = _turns(events)
    (remainder,) = [e for e in events if e["kind"] == "usage_remainder"]
    assert remainder == {"kind": "usage_remainder", "t": None, "input": 0, "output": 582, "cache_read": 0,
                         "cache_write": 0}
    assert [remainder[f] + sum(t[f] for t in turns) for f in ("input", "output", "cache_read", "cache_write")] == [
        6, 600, 270, 30]


def test_the_remainder_of_a_main_scope_run_counts_the_parents_turns_only():
    events = convert("claude-stream-json", _claude_run(tree=False)).events
    (remainder,) = [e for e in events if e["kind"] == "usage_remainder"]
    # the parent's usage: input 4 is 3 + 1 and output 400 is 5 + 9 + 386; the subagent's calls are not in the scope
    assert (remainder["input"], remainder["output"], remainder["cache_write"]) == (0, 386, 0)


def test_a_run_with_no_result_totals_writes_no_remainder():
    raw = _lines(_call("a", _usage(3, 1, 0, 0), "2026-11-14T10:00:00Z"))
    assert [e["kind"] for e in convert("claude-stream-json", raw).events] == ["turn"]


def test_a_remainder_is_null_for_a_field_a_turn_does_not_report():
    raw = _lines(_call("a", {"output_tokens": 4}, "2026-11-14T10:00:00Z"),
                 {"type": "result", "result": "", "num_turns": 1, "is_error": False, "subtype": "success",
                  "usage": {"input_tokens": 9, "output_tokens": 4},
                  "modelUsage": {"m": {"inputTokens": 9, "outputTokens": 4}}})
    (remainder,) = [e for e in convert("claude-stream-json", raw).events if e["kind"] == "usage_remainder"]
    assert (remainder["input"], remainder["output"]) == (None, 0)


def test_a_compaction_follows_the_turn_it_came_after():
    events = convert("claude-stream-json", _claude_run(tree=True)).events
    assert [e for e in events if e["kind"] == "compaction"] == [{"kind": "compaction", "t": 3.0, "turn": 0}]


def test_a_call_without_usage_writes_no_turn_and_a_compaction_after_it_has_none():
    raw = _lines(
        {"type": "assistant", "timestamp": "2026-11-14T10:00:00Z",
         "message": {"id": "a1", "model": "m", "content": [], "usage": None}},
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-14T10:00:05Z"},
    )
    events = convert("claude-stream-json", raw).events
    assert _turns(events) == []
    assert [e for e in events if e["kind"] == "compaction"] == [{"kind": "compaction", "t": 5.0, "turn": None}]


def test_a_compaction_before_any_call_has_no_turn():
    raw = _lines({"type": "system", "subtype": "compact_boundary", "timestamp": "2026-11-14T10:00:00Z"})
    assert [e for e in convert("claude-stream-json", raw).events if e["kind"] == "compaction"] == [
        {"kind": "compaction", "t": 0.0, "turn": None}]


def test_a_codex_turn_with_only_cached_input_reports_that_count():
    raw = _lines({"type": "turn.completed", "usage": {"cached_input_tokens": 5}})
    assert _turns(convert("codex-json", raw).events) == [
        {"kind": "turn", "t": None, "turn": 0, "input": None, "output": None, "cache_read": 5, "cache_write": None,
         "prompt_tokens": None}]


def test_codex_turns_come_from_turn_completed_and_a_missing_count_is_null():
    raw = _lines(
        {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 4, "output_tokens": 3}},
        {"type": "turn.completed", "usage": {"input_tokens": 6, "output_tokens": 2}},
        {"type": "turn.completed"},
    )
    transcript = convert("codex-json", raw)
    turns = _turns(transcript.events)
    assert turns == [
        {"kind": "turn", "t": None, "turn": 0, "input": 6, "output": 3, "cache_read": 4, "cache_write": None,
         "prompt_tokens": 10},
        {"kind": "turn", "t": None, "turn": 1, "input": 6, "output": 2, "cache_read": None, "cache_write": None,
         "prompt_tokens": 6},
        {"kind": "turn", "t": None, "turn": 2, "input": None, "output": None, "cache_read": None, "cache_write": None,
         "prompt_tokens": None},
    ]
    assert transcript.input_tokens == 12 and transcript.output_tokens == 5 and transcript.cache_read_tokens == 4


def test_copilot_writes_no_turn_events():
    raw = _lines({"type": "assistant.message", "data": {"content": "done"}})
    assert _turns(convert("copilot-json", raw).events) == []


def test_turn_and_compaction_events_are_not_tool_calls_or_tool_path_steps():
    events = convert("claude-stream-json", _claude_run(tree=True)).events
    tool_only = [e for e in events if e["kind"] in TOOL_KINDS]
    assert len(tool_only) < len(events)  # the run has turn and compaction events
    assert process_metrics(events, lambda _: False)["tool_calls"] == len(tool_only)
    assert run_from_events(events).kinds() == run_from_events(tool_only).kinds()


def test_every_turn_and_compaction_line_validates_against_the_schema():
    events = convert("claude-stream-json", _claude_run(tree=True)).events
    for event in events:
        if event["kind"] in ("turn", "compaction", "usage_remainder"):
            assert schema_errors("repo_event.v2", {**event, "v": 2}) == [], event


def test_a_turn_without_its_counts_does_not_validate():
    turn = {"kind": "turn", "t": None, "turn": 0, "input": 1, "output": 1}  # no cache or prompt counts
    assert schema_errors("repo_event.v2", {**turn, "v": 2}) != []
    assert schema_errors("repo_event.v2", {**turn, "v": 2, "cache_read": None, "cache_write": None,
                                           "prompt_tokens": None}) == []


def test_a_turn_without_its_counts_does_not_validate_against_v2():
    turn = {"kind": "turn", "t": None, "turn": 0, "input": 1, "output": 1}  # an integer output is valid; the counts are missing
    errors = schema_errors("repo_event.v2", {**turn, "v": 2})
    assert errors != []
    assert all(key in " ".join(errors) for key in ("cache_read", "cache_write", "prompt_tokens"))
    full = {**turn, "cache_read": None, "cache_write": None, "prompt_tokens": 1}
    assert schema_errors("repo_event.v2", {**full, "v": 2}) == []


def test_a_claude_usage_missing_counts_are_null_not_zero():
    raw = _lines(_call("a", {"input_tokens": 10, "output_tokens": 2}, "2026-11-14T10:00:00Z"),
                 _call("b", {"input_tokens": 5}, "2026-11-14T10:00:01Z"),
                 _call("b", {"output_tokens": 4}, "2026-11-14T10:00:02Z"),
                 _call("c", {"input_tokens": 3}, "2026-11-14T10:00:03Z"),
                 _call("d", {"input_tokens": 1, "output_tokens": 0}, "2026-11-14T10:00:04Z"))
    assert _turns(convert("claude-stream-json", raw).events) == [
        {"kind": "turn", "t": 0.0, "turn": 0, "input": 10, "output": 2, "cache_read": None, "cache_write": None,
         "prompt_tokens": 10},
        {"kind": "turn", "t": 1.0, "turn": 1, "input": 5, "output": 4, "cache_read": None, "cache_write": None,
         "prompt_tokens": 5},
        {"kind": "turn", "t": 3.0, "turn": 2, "input": 3, "output": None, "cache_read": None, "cache_write": None,
         "prompt_tokens": 3},
        {"kind": "turn", "t": 4.0, "turn": 3, "input": 1, "output": 0, "cache_read": None, "cache_write": None,
         "prompt_tokens": 1},
    ]


def _result(usage: dict) -> dict:
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "result": "ok", "usage": usage}


def test_a_zero_output_closes_the_remainder_to_zero_not_null():
    raw = _lines(_call("a", {"input_tokens": 2, "output_tokens": 0}, "2026-11-14T10:00:00Z"),
                 _result({"input_tokens": 2, "output_tokens": 0}))
    remainder = [e for e in convert("claude-stream-json", raw).events if e["kind"] == "usage_remainder"][0]
    assert remainder["output"] == 0 and remainder["input"] == 0


def test_a_result_that_omits_a_cache_count_leaves_the_remainder_null():
    call = _call("a", {"input_tokens": 2, "cache_read_input_tokens": 30, "output_tokens": 4}, "2026-11-14T10:00:00Z")
    omitted = convert("claude-stream-json", _lines(call, _result({"input_tokens": 2, "output_tokens": 4})))
    remainder = [e for e in omitted.events if e["kind"] == "usage_remainder"][0]
    assert remainder["cache_read"] is None  # the result has no total for it, so 0 - 30 would be a made-up count
    reported = _result({"input_tokens": 2, "output_tokens": 4, "cache_read_input_tokens": 30})
    closed = convert("claude-stream-json", _lines(call, reported))
    assert [e for e in closed.events if e["kind"] == "usage_remainder"][0]["cache_read"] == 0


def test_a_tree_result_model_that_omits_a_count_leaves_that_total_null():
    call = _call("a", {"input_tokens": 2, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 0,
                       "output_tokens": 4}, "2026-11-14T10:00:00Z")
    result = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "result": "ok",
              "usage": {"input_tokens": 2, "output_tokens": 4},
              "modelUsage": {"main": {"inputTokens": 2, "outputTokens": 4, "cacheReadInputTokens": 30,
                                      "cacheCreationInputTokens": 0},
                             "helper": {"inputTokens": 1, "outputTokens": 1, "cacheReadInputTokens": 0}}}
    remainder = [e for e in convert("claude-stream-json", _lines(call, result)).events
                 if e["kind"] == "usage_remainder"][0]
    assert remainder["cache_read"] == 0  # every model reports it, so the sum is known
    assert remainder["cache_write"] is None  # the helper does not report it


def test_a_result_with_a_null_count_leaves_the_remainder_null():
    call = _call("a", {"input_tokens": 2, "cache_read_input_tokens": 30, "output_tokens": 4}, "2026-11-14T10:00:00Z")
    result = _result({"input_tokens": 2, "output_tokens": 4, "cache_read_input_tokens": None})
    remainder = [e for e in convert("claude-stream-json", _lines(call, result)).events
                 if e["kind"] == "usage_remainder"][0]
    assert remainder["cache_read"] is None


def test_a_streamed_message_counts_its_largest_output_once():
    raw = _lines(_call("a", {"input_tokens": 4, "output_tokens": 3}, "2026-11-14T10:00:00Z"),
                 _call("a", {"output_tokens": 7}, "2026-11-14T10:00:00.200Z"),
                 _call("a", {"output_tokens": 7}, "2026-11-14T10:00:00.400Z"))
    turns = _turns(convert("claude-stream-json", raw).events)
    assert [(t["turn"], t["output"]) for t in turns] == [(0, 7)]


def test_an_output_only_claude_usage_leaves_input_and_prompt_tokens_null():
    turn = _turns(convert("claude-stream-json", _lines(_call("a", {"output_tokens": 3}, "2026-11-14T10:00:00Z")))
                  .events)[0]
    assert turn["input"] is None and turn["prompt_tokens"] is None and turn["output"] == 3


def test_a_split_message_prompt_tokens_is_the_sum_of_its_parts():
    raw = _lines(_call("a", {"input_tokens": 5, "output_tokens": 1, "cache_creation_input_tokens": 0},
                       "2026-11-14T10:00:00Z"),
                 _call("a", {"cache_read_input_tokens": 10}, "2026-11-14T10:00:01Z"))
    turn = _turns(convert("claude-stream-json", raw).events)[0]
    assert turn["input"] == 5 and turn["cache_read"] == 10 and turn["cache_write"] == 0
    assert turn["prompt_tokens"] == 15


def test_a_codex_usage_with_one_side_reports_the_other_as_null():
    raw = _lines({"type": "turn.completed", "usage": {"input_tokens": 6}},
                 {"type": "turn.completed", "usage": {"output_tokens": 2}})
    transcript = convert("codex-json", raw)
    assert [[t[k] for k in ("input", "output", "prompt_tokens")] for t in _turns(transcript.events)] == [
        [6, None, 6], [None, 2, None]]
    assert transcript.input_tokens == 6 and transcript.output_tokens == 2


def test_a_turn_of_a_non_string_parent_tool_use_id_validates_against_the_schema():
    raw = _lines(_call("a", _usage(3, 1, 0, 0), "2026-11-14T10:00:00Z", parent=7))
    turns = _turns(convert("claude-stream-json", raw).events)
    assert len(turns) == 1 and turns[0]["agent"] == "7"
    assert schema_errors("repo_event.v2", {**turns[0], "v": 2}) == []


def test_a_turn_before_the_first_useful_command_is_not_a_step():
    from dokimi.repos.useful import first_useful
    from dokimi.repos.useful_spec import parse_useful
    raw = _lines({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 1}},
                 {"type": "item.completed", "item": {"id": "c", "type": "command_execution", "command": "pytest -q",
                                                     "aggregated_output": "", "exit_code": 0, "status": "completed"}})
    events = convert("codex-json", raw).events
    assert [e["kind"] for e in events][:2] == ["turn", "command"]
    assert first_useful(parse_useful({"command": "pytest"}), events)["steps"] == 1


def test_a_codex_turn_with_only_cached_input_sets_the_scope_of_its_totals():
    transcript = convert("codex-json", _lines({"type": "turn.completed", "usage": {"cached_input_tokens": 5}}))
    assert (transcript.cache_read_tokens, transcript.usage_scope) == (5, "main")


def test_a_claude_turn_carries_its_streamed_output_and_the_remainder_holds_the_rest_of_the_billed_output():
    raw = _lines(_call("a", _usage(2, 6, 12836, 3476), "2026-11-14T10:00:00Z", blocks=[{"type": "text", "text": "x"}]),
                 {"type": "result", "result": "x", "num_turns": 1, "is_error": False, "subtype": "success",
                  "usage": {"input_tokens": 2, "output_tokens": 353, "cache_read_input_tokens": 12836,
                            "cache_creation_input_tokens": 3476}})
    transcript = convert("claude-stream-json", raw)
    (turn,) = _turns(transcript.events)
    (remainder,) = [e for e in transcript.events if e["kind"] == "usage_remainder"]
    assert turn["output"] == 6 and transcript.output_tokens == 353
    assert remainder["output"] == 347  # the billed output the streamed count does not carry
    assert [turn[f] for f in TURN_FIELDS] == [
        transcript.input_tokens, transcript.cache_read_tokens, transcript.cache_write_tokens]


LIVE_FIXTURE = Path(__file__).parent / "fixtures" / "claude_stream" / "live_usage.jsonl"


def test_a_live_shaped_claude_trace_turns_match_the_parents_top_level_usage_and_the_remainder_closes_the_totals():
    """A sanitized stream-json run (the live run's record shape, synthetic counts): three calls, each message
    streamed as two or three records with one usage; a helper model's 900 input tokens leave no record."""
    transcript = convert("claude-stream-json", LIVE_FIXTURE.read_text(encoding="utf-8"))
    turns = _turns(transcript.events)
    (remainder,) = [e for e in transcript.events if e["kind"] == "usage_remainder"]
    top_level = {"input": 6, "cache_read": 640, "cache_write": 390}  # the result's usage block
    assert [t["turn"] for t in turns] == [0, 1, 2]
    assert [t["output"] for t in turns] == [20, 2, 5]  # each call's merged count, not the sum of its records
    for field, billed in top_level.items():
        assert sum(t[field] for t in turns) == billed
    assert transcript.usage_scope == "tree"
    assert remainder == {"kind": "usage_remainder", "t": None, "input": 900, "output": 973, "cache_read": 0,
                         "cache_write": 0}
    assert schema_errors("repo_event.v2", {**remainder, "v": 2}) == []
