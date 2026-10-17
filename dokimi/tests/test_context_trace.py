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
    (data,) = json.loads(got.out)  # a list for one trace too
    assert data["trace"] == str(a) and data["peak_prompt_tokens"] == 20815
    assert "per_call" not in data and "compaction_events" not in data
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


def test_bom_and_undecodable_bytes_do_not_lose_the_rest(tmp_path):
    good = json.dumps(INIT) + chr(10) + json.dumps(_assistant("m1", _usage(5, 0, 100))) + chr(10)
    bom = tmp_path / "bom.jsonl"
    bom.write_bytes(b"\xef\xbb\xbf" + good.encode())
    got = read_trace(bom)
    assert got.errors == [] and got.surface["tools"] and len(got.calls) == 1
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(good.encode() + b"\xff\xfe" + chr(10).encode())
    got = read_trace(bad)
    assert len(got.calls) == 1 and len(got.errors) == 1


def test_utf16_trace_is_a_trace_error_not_a_traceback(tmp_path):
    path = tmp_path / "u16.jsonl"
    path.write_bytes(json.dumps(INIT).encode("utf-16"))
    with pytest.raises(TraceError):
        read_trace(path)


def test_infinite_usage_is_a_line_warning_not_a_crash():
    raw = '{"type":"assistant","message":{"id":"m","usage":{"input_tokens":Infinity}}}'
    got = parse_trace(raw + chr(10) + json.dumps(_assistant("m2", _usage(5, 0, 1))))
    assert len(got.calls) == 1 and "line 1: unexpected shape" in got.errors[0]


# a repeated message id: a later, sparser block must not erase what an earlier one recorded

@pytest.mark.parametrize("later", [{}, {"output_tokens": 90}, {"input_tokens": 3}])
def test_a_sparse_later_block_keeps_the_prompt(later):
    got = parse_trace(_jsonl(_assistant("m1", _usage(3, 100, 5000)), _assistant("m1", later)))
    (call,) = got.calls
    assert (call["prompt_tokens"], call["new_tokens"]) == (5103, 103)
    assert call["output_tokens"] == max(1, later.get("output_tokens", 0))


def test_a_later_block_keeps_the_thread_of_the_call():
    got = parse_trace(_jsonl(_assistant("m1", _usage(3, 100, 5000)), _assistant("m1", {}, agent="toolu_x")))
    assert got.summary()["calls"] == 1 and got.calls[0]["agent"] is None


def test_an_all_zero_block_is_not_a_call():
    zero = {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 0}
    got = parse_trace(_jsonl(_assistant("m1", zero), _assistant("m1", _usage(3, 100, 5000)),
                             _assistant("m2", _usage(3, 100, 9000)), _assistant("m3", zero)))
    s = got.summary()
    assert s["calls"] == 2 and s["first_prompt_tokens"] == 5103 and s["final_prompt_tokens"] == 9103


# repeats: identical, successful, and nothing in between that could change the answer

def _repeats(*steps):
    """steps: (name, input, error) tool calls on the main thread, each with its result."""
    records = []
    for i, (name, args, error) in enumerate(steps):
        records.append(_assistant(f"m{i}", _usage(1, 0, 0), _tool_use(f"t{i}", name, **args)))
        records.append(_result(f"t{i}", "out", error=error))
    return parse_trace(_jsonl(*records)).summary()


def test_the_same_read_twice_is_a_repeat():
    assert _repeats(("Read", {"file_path": "/a"}, False), ("Read", {"file_path": "/a"}, False))[
        "repeated_tool_calls"] == 1


def test_a_repeat_needs_the_same_name_and_the_same_input():
    s = _repeats(("Read", {"file_path": "/a"}, False), ("Grep", {"file_path": "/a"}, False),
                 ("Read", {"file_path": "/b"}, False))
    assert s["repeated_tool_calls"] == 0


def test_a_retry_after_a_failure_is_not_a_repeat():
    s = _repeats(("Bash", {"command": "make test"}, True), ("Bash", {"command": "make test"}, False))
    assert (s["failed_tool_calls"], s["repeated_tool_calls"]) == (1, 0)


def test_a_read_after_an_edit_is_not_a_repeat():
    s = _repeats(("Read", {"file_path": "/a"}, False), ("Edit", {"file_path": "/a"}, False),
                 ("Read", {"file_path": "/a"}, False))
    assert s["repeated_tool_calls"] == 0


def test_failed_counts_calls_not_tools():
    s = _repeats(("Read", {"file_path": "/a"}, True), ("Read", {"file_path": "/b"}, True),
                 ("Read", {"file_path": "/c"}, False))
    assert (s["failed_tool_calls"], s["repeated_tool_calls"]) == (2, 0)


# the summary's limits and order

def _sized(*sizes):
    """One call per size, each to its own tool, whose result has that many characters."""
    records = []
    for i, size in enumerate(sizes):
        records.append(_assistant(f"m{i}", _usage(1, 0, 0), _tool_use(f"t{i}", f"T{i}")))
        records.append(_result(f"t{i}", "x" * size))
    return _jsonl(*records)


def test_largest_tool_results_are_the_five_heaviest_in_order_without_empty_ones():
    top = parse_trace(_sized(10, 0, 60, 30, 50, 20, 40)).summary()["largest_tool_results"]
    assert [t["tool"] for t in top] == ["T2", "T4", "T6", "T3", "T5"]
    assert all(t["chars"] for t in parse_trace(_sized(10, 0)).summary()["largest_tool_results"])


def test_cli_names_the_three_heaviest_tools(tmp_path, capsys):
    a = tmp_path / "a.jsonl"
    a.write_text(_sized(10, 40, 30, 20), encoding="utf-8")
    out = _run_cli(["context", "trace", str(a)], capsys).out
    assert f"{a}: largest tool results (chars): T1 40, T2 30, T3 20" in out.splitlines()


# compaction shapes

def test_camel_case_and_bare_compactions():
    got = parse_trace(_jsonl(
        _assistant("m1", _usage(1, 0, 0)),
        {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "manual", "preTokens": 7}},
        {"type": "system", "subtype": "compact_boundary"},
    ))
    assert got.compactions == [
        {"trigger": "manual", "pre_tokens": 7, "post_tokens": None, "after_call": 0},
        {"trigger": None, "pre_tokens": None, "post_tokens": None, "after_call": 0},
    ]


# partial traces and the table's edges

def test_an_init_only_trace_reads_with_empty_numbers(tmp_path, capsys):
    a = tmp_path / "a.jsonl"
    a.write_text(_jsonl(INIT), encoding="utf-8")
    out = _run_cli(["context", "trace", str(a)], capsys).out
    assert out.splitlines()[0] == "| trace | calls | first | peak | growth | compact | tools | failed | repeat | result chars |"
    assert f"| {a} | 0 | – | – | – | 0 | 0 | 0 | 0 | 0 |" in out


def test_an_assistant_only_trace_has_no_surface():
    s = parse_trace(_jsonl(_assistant("m1", _usage(1, 2, 3)))).summary()
    assert s["surface"] == {} and s["harness_version"] is None and s["calls"] == 1


def test_a_pipe_in_a_trace_name_stays_in_its_cell(tmp_path, capsys):
    a = tmp_path / "a|b.jsonl"
    a.write_text(_jsonl(_assistant("m1", _usage(1, 0, 0))), encoding="utf-8")
    row = _run_cli(["context", "trace", str(a)], capsys).out.splitlines()[2]
    assert row.startswith(f"| {str(a).replace('|', chr(92) + '|')} | 1 |")


def test_one_unreadable_trace_does_not_lose_the_others(tmp_path, capsys):
    a, empty = tmp_path / "a.jsonl", tmp_path / "empty.jsonl"
    a.write_text(RUN, encoding="utf-8")
    empty.write_text("", encoding="utf-8")
    for extra in ([], ["--json"]):
        with pytest.raises(SystemExit) as exc:
            _run_cli(["context", "trace", str(empty), str(a), *extra], capsys)
        got = capsys.readouterr()
        assert exc.value.code == 1 and "empty.jsonl" in got.err
        if extra:
            assert [r["trace"] for r in json.loads(got.out)] == [str(a)]
        else:
            assert f"| {a} | 5 |" in got.out


@pytest.mark.parametrize("line", ["[" * 100_000, '{"n": ' + "9" * 5000 + "}"], ids=["deep", "long-int"])
def test_json_that_python_cannot_load_is_a_line_warning(line):
    got = parse_trace(line + chr(10) + json.dumps(_assistant("m1", _usage(1, 0, 0))))
    assert got.errors == ["line 1: not JSON"] and len(got.calls) == 1
