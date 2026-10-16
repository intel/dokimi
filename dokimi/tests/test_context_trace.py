# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`context trace`: the prompt size of every model call, read from a Claude Code stream-json trace."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.context import TraceError, read_trace
from dokimi.context.trace import parse_trace


def _usage(fresh, created, read, out=1):
    return {"input_tokens": fresh, "cache_creation_input_tokens": created,
            "cache_read_input_tokens": read, "output_tokens": out}


def _assistant(mid, usage, *blocks, agent=None):
    return {"type": "assistant", "parent_tool_use_id": agent,
            "message": {"id": mid, "usage": usage, "content": list(blocks)}}


def _tool_use(tid, name, **args):
    return {"type": "tool_use", "id": tid, "name": name, "input": args}


def _result(tid, text, error=False, agent=None):
    return {"type": "user", "parent_tool_use_id": agent,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text,
                                     "is_error": error}]}}


INIT = {"type": "system", "subtype": "init", "claude_code_version": "2.1.296", "model": "m",
        "tools": ["Bash", "Read", "Task"], "mcp_servers": [{"name": "docs", "status": "connected"}],
        "skills": ["a", "b"], "agents": ["Explore"], "plugins": [{"name": "cc-plugin-agents-md"}]}


def _jsonl(*records):
    return "".join(json.dumps(r) + "\n" for r in records)


RUN = _jsonl(
    INIT,
    # one call written as two messages (thinking, then the tool use): counted once
    _assistant("m1", _usage(10, 20000, 0), {"type": "thinking", "thinking": ""}),
    _assistant("m1", _usage(10, 20000, 0, out=40), _tool_use("t1", "Bash", command="ls")),
    _result("t1", "x" * 300),
    _assistant("m2", _usage(5, 400, 20010), _tool_use("t2", "Bash", command="ls")),  # same call again
    _result("t2", "x" * 300),
    _assistant("m3", _usage(5, 400, 20410), _tool_use("t3", "Read", file_path="/nope")),
    _result("t3", "File does not exist.", error=True),
    {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "auto", "pre_tokens": 20815,
                                                                          "post_tokens": 9000}},
    _assistant("m4", _usage(5, 3000, 9000), _tool_use("t4", "Task", prompt="look")),
    # the subagent's own window: counted apart, and its `ls` is not a repeat of the main thread's
    _assistant("s1", _usage(5, 5000, 0), _tool_use("t5", "Bash", command="ls"), agent="t4"),
    _result("t5", "y" * 50, agent="t4"),
    _result("t4", "done"),
    _assistant("m5", _usage(5, 100, 12000), {"type": "text", "text": "ok"}),
    {"type": "result", "subtype": "success", "num_turns": 5, "is_error": False},
)


def test_counts_one_call_per_message_id_and_sums_the_three_input_parts():
    trace = parse_trace(RUN)
    main = trace.main_calls()
    assert [c["prompt_tokens"] for c in main] == [20010, 20415, 20815, 12005, 12105]
    assert main[0]["output_tokens"] == 40  # the later block of the same call wins
    assert main[1]["new_tokens"] == 405


def test_summary_reads_the_main_thread():
    s = parse_trace(RUN).summary(window=100_000)
    assert s["calls"] == 5 and s["subagent_calls"] == 1
    assert (s["first_prompt_tokens"], s["peak_prompt_tokens"], s["final_prompt_tokens"]) == (20010, 20815, 12105)
    assert s["growth_tokens"] == 805
    assert s["peak_fill"] == pytest.approx(0.2082, abs=1e-4)
    assert s["compactions"] == 1
    assert s["surface"] == {"tools": 3, "mcp_servers": ["docs"], "skills": 2, "agents": 1,
                            "plugins": ["cc-plugin-agents-md"]}
    assert s["num_turns"] == 5 and s["result_subtype"] == "success"


def test_tool_results_failures_and_repeats():
    trace = parse_trace(RUN)
    s = trace.summary()
    assert s["tool_calls"] == 5
    assert s["failed_tool_calls"] == 1
    assert s["repeated_tool_calls"] == 1
    assert s["tool_result_chars"] == 300 + 300 + len("File does not exist.") + 50 + 4
    assert s["largest_tool_results"][0] == {"tool": "Bash", "calls": 3, "chars": 650, "max_chars": 300}
    assert trace.compactions == [{"trigger": "auto", "pre_tokens": 20815, "post_tokens": 9000, "after_call": 2}]


def test_without_a_window_there_is_no_fill():
    assert parse_trace(RUN).summary()["peak_fill"] is None


def test_bad_lines_are_reported_and_skipped():
    trace = parse_trace("not json\n[1]\n" + _jsonl(
        _assistant("m1", _usage(1, 2, 3)),
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": ["text", 1]}]}},
    ))
    assert trace.errors == ["line 1: not JSON"]
    assert [c["prompt_tokens"] for c in trace.calls] == [6]


def test_list_content_counts_text_blocks_only():
    trace = parse_trace(_jsonl(
        _assistant("m1", _usage(1, 0, 0), _tool_use("t1", "Read", file_path="a.png")),
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "text", "text": "abc"}, {"type": "image", "source": {}}]}]}},
    ))
    assert trace.tools["Read"].result_chars == 3


def test_a_file_that_is_not_a_trace_is_an_error(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"type": "result"}\n', encoding="utf-8")
    with pytest.raises(TraceError, match="no assistant message"):
        read_trace(path)
    with pytest.raises(TraceError, match="cannot read"):
        read_trace(tmp_path / "missing.jsonl")


def _run_cli(argv, capsys):
    args = build_parser().parse_args(argv)
    args.func(args)
    return capsys.readouterr()


def test_cli_table_compares_traces(tmp_path, capsys):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(RUN, encoding="utf-8")
    b.write_text(_jsonl(INIT, _assistant("m1", _usage(1, 30000, 0))), encoding="utf-8")
    out = _run_cli(["context", "trace", str(a), str(b), "--window", "100000", "--calls"], capsys).out
    rows = [line for line in out.splitlines() if line.startswith("| ")]
    assert rows[0].startswith("| trace | calls | first | peak | fill |")
    assert f"| {a} | 5 | 20,010 | 20,815 | 21% | 805 | 1 | 5 | 1 | 1 |" in rows[1]
    assert f"| {b} | 1 | 30,001 | 30,001 | 30% | 0 | 0 | 0 | 0 | 0 | 0 |" == rows[2]
    assert f"{a}: prompt tokens per call: 20010 20415 20815 12005 12105" in out
    assert f"{a}: largest tool results (chars): Bash 650" in out


def test_cli_json_single_trace(tmp_path, capsys):
    a = tmp_path / "a.jsonl"
    a.write_text("garbage\n" + RUN, encoding="utf-8")
    got = _run_cli(["context", "trace", str(a), "--json"], capsys)
    data = json.loads(got.out)
    assert data["trace"] == str(a) and data["peak_prompt_tokens"] == 20815
    assert "per_call" not in data
    assert "line 1: not JSON" in got.err


def test_cli_missing_trace_exits_1(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        _run_cli(["context", "trace", str(tmp_path / "missing.jsonl")], capsys)
    assert exc.value.code == 1


@pytest.mark.parametrize("sep", [chr(0x2028), chr(0x2029), chr(0x85)])
def test_unicode_line_separators_inside_a_record_do_not_split_it(sep):
    """json.dumps(ensure_ascii=False) and JSON.stringify keep these raw inside strings."""
    lines = [_assistant("m1", _usage(5, 0, 100), {"type": "text", "text": f"a{sep}b"}),
             _result("t1", f"x{sep}y"),
             _assistant("m2", _usage(5, 0, 300))]
    raw = chr(10).join(json.dumps(r, ensure_ascii=False) for r in lines) + chr(10)
    got = parse_trace(raw)
    assert got.errors == []
    assert [c["prompt_tokens"] for c in got.calls] == [105, 305]
    assert sum(s.result_chars for s in got.tools.values()) == 3
