# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A Copilot CLI ``grep`` tool call over a guidance file, in ``guidance_searched`` (contract §4.1). No LLM."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dokimi.repos.events import convert
from dokimi.repos.process_metrics import process_metrics

WS = "/ws"
HIT = "AGENTS.md:3:- warn: always lint"


def _jsonl(*records: dict[str, Any]) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


def _call(call_id: str, name: str, arguments: Any, complete: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    start = {"type": "tool.execution_start", "data": {"toolCallId": call_id, "toolName": name, "arguments": arguments}}
    done = {"type": "tool.execution_complete", "data": {"toolCallId": call_id, "success": True, **(complete or {})}}
    return [start, done]


# The grep arguments follow the Copilot CLI 1.0.75 argument schema (`paths`, `output_mode`); this is not a recorded trace.
def _grep(arguments: dict[str, Any], content: str | None = HIT, success: bool = True) -> list[dict[str, Any]]:
    complete: dict[str, Any] = {"success": success}
    if content is not None:
        complete["result"] = {"content": content}
    return _call("g", "grep", {"pattern": "warn", **arguments}, complete)


def _searched(records: list[dict[str, Any]], cwd: str = "") -> list[str]:
    t = convert("copilot-json", _jsonl(*records), WS, cwd)
    return process_metrics(t.events, lambda p: p.endswith("AGENTS.md"), unknown_reads=t.unknown_reads)["guidance_searched"]


def test_a_content_grep_over_a_guidance_file_is_searched_not_read():
    """§4.1 ``guidance_searched``: a Copilot ``grep`` in ``content`` mode over a file is a search, no ``read`` event."""
    with_grep = convert("copilot-json", _jsonl(*_grep({"paths": "AGENTS.md", "output_mode": "content"})), WS)
    assert with_grep.events == [{"t": None, "kind": "tool", "name": "grep", "searched": ["AGENTS.md"]}]
    assert not any(e["kind"] == "read" for e in with_grep.events)
    metrics = process_metrics(with_grep.events, lambda p: p.endswith("AGENTS.md"))
    bare = process_metrics([{"t": None, "kind": "tool", "name": "grep"}], lambda p: p.endswith("AGENTS.md"))
    assert metrics["guidance_searched"] == ["AGENTS.md"]
    assert metrics["guidance_read"] == [] and metrics["tool_calls"] == bare["tool_calls"]


def test_a_content_grep_over_a_list_of_paths_lists_each_string_entry():
    """§4.1 ``guidance_searched``: ``paths`` is one string or a list; an empty or non-string entry names nothing."""
    paths = ["AGENTS.md", "", 7, None, "docs/AGENTS.md"]
    assert _searched(_grep({"paths": paths, "output_mode": "content"})) == ["AGENTS.md", "docs/AGENTS.md"]
    for nothing in ([], [""], [3], 5, ""):
        t = convert("copilot-json", _jsonl(*_grep({"paths": nothing, "output_mode": "content"})), WS)
        assert t.events == [{"t": None, "kind": "tool", "name": "grep"}]


@pytest.mark.parametrize("arguments", [
    {"paths": "AGENTS.md"},
    {"paths": "AGENTS.md", "output_mode": "files_with_matches"},
    {"paths": "AGENTS.md", "output_mode": "count"},
    {"output_mode": "content"},
    {"path": "AGENTS.md", "output_mode": "content"},
])
def test_a_grep_that_prints_no_line_of_a_file_is_no_search(arguments):
    """§4.1 ``guidance_searched``: the default mode, ``files_with_matches``, ``count`` and a call with no ``paths``."""
    t = convert("copilot-json", _jsonl(*_grep(arguments)), WS)
    assert t.events == [{"t": None, "kind": "tool", "name": "grep"}]


@pytest.mark.parametrize("content, success", [
    ("No matches found.", True), ("  No matches found.\n", True), ("", True), (HIT, False)])
def test_a_grep_with_no_match_or_a_failure_is_no_search(content, success):
    """§4.1 ``guidance_searched``: a no-match, empty or failed result drops the search."""
    records = _grep({"paths": "AGENTS.md", "output_mode": "content"}, content, success)
    assert _searched(records) == []
    assert convert("copilot-json", _jsonl(*records), WS).events == [{"t": None, "kind": "tool", "name": "grep"}]


def test_a_grep_whose_result_is_not_in_the_trace_keeps_its_search():
    records = _grep({"paths": "AGENTS.md", "output_mode": "content"})[:1]
    assert _searched(records) == ["AGENTS.md"]


def test_a_directory_in_paths_is_kept_but_not_counted():
    """§4.1 ``guidance_searched``: ``process_metrics`` keeps guidance files only, so a directory is not counted."""
    records = _grep({"paths": ["docs"], "output_mode": "content"})
    assert convert("copilot-json", _jsonl(*records), WS).events[0]["searched"] == ["docs"]
    assert _searched(records) == []


def test_a_relative_path_starts_at_the_start_directory_as_a_views_does():
    """§4.1 ``guidance_searched``: a relative ``paths`` entry is resolved from ``cwd``, as ``view``'s ``path`` is."""
    records = _grep({"paths": "AGENTS.md", "output_mode": "content"})
    assert _searched(records, "sub") == ["sub/AGENTS.md"]
    view = _call("v", "view", {"path": "AGENTS.md"})
    assert convert("copilot-json", _jsonl(*view), WS, "sub").reads() == {"sub/AGENTS.md"}


def test_a_relative_path_after_a_cd_in_an_earlier_bash_call_is_resolved_as_a_views_is():
    """§4.1 ``guidance_searched``: the Copilot converter does not track a moved shell, for ``view`` nor ``grep``."""
    cd = _call("c", "bash", {"command": "cd sub"}, {"shellExecution": {"exitCode": 0}})
    view = _call("v", "view", {"path": "AGENTS.md"})
    assert convert("copilot-json", _jsonl(*cd, *view), WS).reads() == {"AGENTS.md"}
    assert _searched([*cd, *_grep({"paths": "AGENTS.md", "output_mode": "content"})]) == ["AGENTS.md"]


def test_a_grep_with_arguments_that_are_not_an_object_stays_a_bare_tool_event():
    t = convert("copilot-json", _jsonl(*_call("g", "grep", "warn AGENTS.md")), WS)
    assert t.events == [{"t": None, "kind": "tool", "name": "grep"}] and t.dropped_lines == 0
