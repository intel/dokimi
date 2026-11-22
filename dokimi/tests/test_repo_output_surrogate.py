# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A command's output with a surrogate (a JSON ``\\ud800`` escape) is clipped, not raised on (#884)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.repos.events import convert
from dokimi.repos.transcript import OUTPUT_KEEP, OUTPUT_LIMIT, clip_output

LONE_HIGH = chr(0xD800)
LONE_LOW = chr(0xDCFF)


@pytest.mark.parametrize("lone", [LONE_HIGH, LONE_LOW])
def test_a_lone_surrogate_in_a_short_output_is_written_as_a_question_mark(lone):
    """#884: ``clip_output`` encoded strictly and raised UnicodeEncodeError."""
    assert clip_output("n" + lone) == ("n?", False)


def test_a_surrogate_pair_is_joined_into_its_character():
    """#884: a JSON escape pair is one character, as PyYAML and ``json`` load it."""
    assert clip_output("a" + chr(0xD83D) + chr(0xDE00)) == ("a😀", False)


def test_an_output_of_exactly_the_limit_is_kept_whole():
    """#884: an output of exactly ``OUTPUT_LIMIT`` bytes is kept whole, not clipped."""
    assert clip_output("x" * OUTPUT_LIMIT) == ("x" * OUTPUT_LIMIT, False)


def test_a_pair_cut_at_the_end_of_the_head_is_dropped_whole():
    """#884: a joined pair is one 4-byte character, so a cut through it drops it whole."""
    clipped, _ = clip_output("a" + (chr(0xD83D) + chr(0xDE00)) * 2100)
    assert clipped.startswith("a" + "\U0001F600" * 1023 + "\n[... ")


def test_a_long_output_with_surrogates_in_the_head_and_the_clipped_middle_is_clipped():
    """#884: the head keeps the lone surrogate as ``?``; the omitted count is of the cleaned bytes."""
    text = "h" + LONE_HIGH + "x" * 20000 + LONE_LOW + "y" * 20000 + "end"
    clipped, truncated = clip_output(text)
    head, rest = clipped.split("\n[... ", 1)
    omitted = int(rest.split(" bytes omitted", 1)[0])
    tail = rest.split("...]\n", 1)[1]
    assert truncated and head.startswith("h?x")
    assert tail.endswith("end") and "?" not in tail
    assert omitted == len(text.encode("utf-8", "replace")) - len(head.encode()) - len(tail.encode())
    assert len(head.encode()) == OUTPUT_KEEP and len(clipped.encode()) < OUTPUT_LIMIT + 100


def test_a_trace_whose_command_output_has_a_lone_surrogate_converts():
    """#884: ``convert`` of a Claude Code trace with a ``\\ud800`` escape in a tool result raised UnicodeEncodeError."""
    use = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "make"}}]}}
    result = {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "@@"}]}}
    raw = "\n".join([json.dumps(use), json.dumps(result).replace("@@", "n\\ud800")])
    assert json.loads(raw.splitlines()[1])["message"]["content"][0]["content"] == "n" + LONE_HIGH
    (event,) = convert("claude-stream-json", raw).events
    assert event["output"] == "n?" and event["output_truncated"] is False


CONTRACT = Path(__file__).resolve().parents[1] / "docs" / "repo-evaluation-contract.md"


def test_the_contract_states_that_a_split_character_is_dropped_whole():
    """#964: a character a cut splits is dropped, so the ends keep up to 4,096 bytes and N counts the rest."""
    text = CONTRACT.read_text(encoding="utf-8")
    section = " ".join(text[text.index("### 4.1 `events.jsonl`") :].split("\n### ", 1)[0].split())
    assert "up to 4,096 bytes at each end" in section
    assert "dropped whole" in section
    head = "é" * 2047 + "h"
    tail = "界" * 1364 + "e"
    clipped, truncated = clip_output(head + "😀MID😀" + tail)
    assert truncated and clipped.split("\n")[0] == head


def test_reviewing_names_the_gold_field_of_task_json():
    """#964: a task sets its gold file with ``gold`` in task.json, so the guide cannot name only gold.json."""
    guide = (CONTRACT.parent / "reviewing.md").read_text(encoding="utf-8")
    para = guide[guide.index("Read the hits before citing one") :][:900]
    assert "`gold` field" in para and "default `gold.json`" in para
