# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A ``..`` after a link in a traced operand is followed as the system does, not collapsed (contract §4.1, #1042)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace
from typing import Any

import pytest

from dokimi.repos import parse_manifest
from dokimi.repos.arms import skill_usage
from dokimi.repos.events import convert
from dokimi.repos.held_guidance import held_guidance
from dokimi.repos.process_metrics import process_metrics
from dokimi.repos.runner import run_process_metrics
from dokimi.repos.shell_walk import shell_reads
from dokimi.repos.transcript import Transcript
from dokimi.repos.uptake import arm_uptake
from test_repo_events_bash import WS, _call, _events, _result
from test_repo_manifest import MANIFEST


@pytest.fixture
def held(tmp_path):
    """A workspace of the issue: ``alias -> docs/nested``, ``sub/alias -> <outside>/nested``, ``src`` a plain directory."""
    ws, outside = tmp_path / "workspace", tmp_path / "outside"
    for d in (ws / "docs/nested", ws / "src", ws / "sub", outside / "nested"):
        d.mkdir(parents=True)
    for f in (ws / "AGENTS.md", ws / "docs/AGENTS.md", ws / "sub/AGENTS.md", outside / "AGENTS.md"):
        f.write_text("warn\n")
    (ws / "alias").symlink_to("docs/nested", target_is_directory=True)
    (ws / "sub/alias").symlink_to(outside / "nested", target_is_directory=True)
    (ws / "loop").symlink_to("loop")
    (ws / "dlink").symlink_to("docs", target_is_directory=True)
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "add", "-A"], check=True)
    return held_guidance(tmp_path, parse_manifest(MANIFEST))


def _lists(events, held) -> tuple[list[str], list[str]]:
    ctx: Any = SimpleNamespace(manifest=parse_manifest(MANIFEST))
    got = run_process_metrics(ctx, events, 0, held)
    return got["guidance_read"], got["guidance_searched"]


def _bash(command: str, held, cwd: str = "") -> tuple[list[str], list[str]]:
    return _lists(_events(_call("a", command), _result("a"), cwd=cwd), held)


@pytest.mark.parametrize("command", ["cat alias/../AGENTS.md", "head -n 5 alias/../AGENTS.md", "cd alias && cat ../AGENTS.md"])
def test_a_read_after_a_directory_link_goes_to_the_files_parent_not_the_names(command, held):
    assert _bash(command, held) == (["docs/AGENTS.md"], [])


@pytest.mark.parametrize("command", ["grep warn alias/../AGENTS.md", "cd alias && grep warn ../AGENTS.md"])
def test_a_search_after_a_directory_link_goes_to_the_files_parent_not_the_names(command, held):
    assert _bash(command, held) == ([], ["docs/AGENTS.md"])


@pytest.mark.parametrize("command", [
    "cat sub/alias/../AGENTS.md", "grep warn sub/alias/../AGENTS.md", "cd sub/alias && cat ../AGENTS.md",
    "cd sub && cd alias && grep warn ../AGENTS.md", "cat loop/../AGENTS.md"])  # a link that loops leads nowhere
def test_a_link_out_of_the_workspace_credits_no_file_of_it(command, held):
    assert _bash(command, held) == ([], [])


@pytest.mark.parametrize("command", [
    "cat src/../AGENTS.md", "cat src/./../AGENTS.md", "cd src && cat ../AGENTS.md", "cat ./src//../AGENTS.md",
    "cat sub/../AGENTS.md", "cat docs/nested/../AGENTS.md"])
def test_a_plain_directory_before_a_dotdot_still_lists_the_normalised_file(command, held):
    read, _ = _bash(command, held)
    assert read == ["docs/AGENTS.md" if "docs" in command else "AGENTS.md"]


def test_a_plain_directory_before_a_dotdot_in_a_search_still_lists_the_normalised_file(held):
    assert _bash("grep warn src/../AGENTS.md", held) == ([], ["AGENTS.md"])


def test_cd_takes_a_dotdot_by_name_as_bash_does_and_cd_p_follows_the_link(held):
    assert _bash("cd alias/.. && cat AGENTS.md", held)[0] == ["AGENTS.md"]
    assert _bash("cd alias && cd .. && grep warn AGENTS.md", held)[1] == ["AGENTS.md"]
    assert _bash("cd -P alias/.. && cat AGENTS.md", held)[0] == ["docs/AGENTS.md"]
    assert _bash("cd -P -L alias/.. && cat AGENTS.md", held)[0] == ["AGENTS.md"]
    assert _bash("cd -L -P alias/.. && cat AGENTS.md", held)[0] == ["docs/AGENTS.md"]


def test_a_cd_after_cd_p_goes_back_past_the_physical_directory_not_the_name(held):
    assert _bash("cd -P alias/.. && cd .. && cat AGENTS.md", held)[0] == ["AGENTS.md"]
    assert _bash("cd -P alias/.. && cd .. && grep warn AGENTS.md", held)[1] == ["AGENTS.md"]
    assert _bash("cd -P alias && cd .. && cat AGENTS.md", held)[0] == ["docs/AGENTS.md"]
    assert _bash("cd -P alias && cd .. && cd .. && cat AGENTS.md", held)[0] == ["AGENTS.md"]


def test_a_cd_after_cd_p_collapses_only_the_names_it_took_itself(held):
    assert _bash("cd -P alias/.. && cd nested && cd .. && cat AGENTS.md", held)[0] == ["docs/AGENTS.md"]
    assert _bash("cd -P alias/.. && cd nested/.. && cat AGENTS.md", held)[0] == ["docs/AGENTS.md"]
    assert _bash("cd -P alias && cd -L .. && cat AGENTS.md", held)[0] == ["docs/AGENTS.md"]


def test_a_cd_goes_on_from_an_absolute_directory_and_not_from_one_not_known():
    assert shell_reads("cd /w/sub && cd .. && cat AGENTS.md") == ["/w/AGENTS.md"]
    assert shell_reads("cd /w && cd .. && cat AGENTS.md") == ["/AGENTS.md"]
    assert shell_reads("cd -P /w/sub && cd .. && cat AGENTS.md") == ["/w/sub/../AGENTS.md"]
    assert shell_reads("cd - ; cd sub && cat AGENTS.md") == []


def test_a_path_through_a_link_without_a_dotdot_is_listed_as_the_agent_wrote_it(held):
    assert _bash("cat dlink/AGENTS.md", held) == (["dlink/AGENTS.md"], [])
    assert _bash("grep warn ./dlink//AGENTS.md", held) == ([], ["dlink/AGENTS.md"])


def test_a_dotdot_that_leaves_the_workspace_is_no_repository_file(held):
    assert _bash("cat ../AGENTS.md", held) == ([], [])
    assert _bash("cat src/../../AGENTS.md", held) == ([], [])


def test_the_converter_keeps_a_dotdot_after_a_name_in_the_read_event():
    events = _events(_call("a", "cat a/../b; cd c && cat ../d"), _result("a"))
    assert [e["path"] for e in events if e["kind"] == "read"] == ["a/../b", "c/../d"]
    assert shell_reads("cat a//./b/../c") == ["a/b/../c"]
    assert shell_reads("cat ../x") == ["../x"]
    assert shell_reads("cat x", "a/../b") == ["a/../b/x"]  # the harness started in a directory named with a ..


def test_a_dotdot_after_a_link_in_a_view_tool_path_is_followed(held):
    def view(path: str, cwd: str = "") -> list[dict]:
        raw = json.dumps({"type": "tool.execution_start",
                          "data": {"toolCallId": "w", "toolName": "view", "arguments": {"path": path}}})
        return convert("copilot-json", raw, WS, cwd).events

    assert _lists(view("alias/../AGENTS.md"), held)[0] == ["docs/AGENTS.md"]
    assert _lists(view("../AGENTS.md", "alias"), held)[0] == ["docs/AGENTS.md"]
    assert _lists(view("sub/alias/../AGENTS.md"), held)[0] == []
    assert _lists(view("..\\AGENTS.md", "src"), held)[0] == ["AGENTS.md"]


def test_a_grep_tool_path_after_a_link_is_followed_from_the_start_directory(held):
    def grep(path: str, cwd: str) -> list[dict]:
        tool = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "g", "name": "Grep",
             "input": {"pattern": "warn", "path": path, "output_mode": "content"}}]}}
        found = {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "g", "content": "1:warn"}]}}
        return convert("claude-stream-json", "\n".join(json.dumps(x) for x in (tool, found)), WS, cwd).events

    assert _lists(grep("../AGENTS.md", "alias"), held)[1] == ["docs/AGENTS.md"]


def test_the_readers_that_know_no_workspace_still_take_a_dotdot_by_name():
    # reach, uptake and the skill usage match a read to a path; they have no links to follow, so they read it as before
    events = _events(_call("a", "cat src/../AGENTS.md; cat .claude/skills/s/../s/SKILL.md"), _result("a"))
    assert Transcript(events=events).reads() == {"AGENTS.md", ".claude/skills/s/SKILL.md"}
    assert arm_uptake(events, ["AGENTS.md"], [], WS)["reads"] == 1
    assert skill_usage(events, ["s"], None)["skills_used"] == ["s"]


def test_without_held_guidance_a_dotdot_is_collapsed_by_name_as_before():
    events = [{"kind": "read", "path": "src/../AGENTS.md", "rc": 0}, {"kind": "read", "path": "a/../../AGENTS.md", "rc": 0}]
    assert process_metrics(events, lambda p: p.endswith("AGENTS.md"))["guidance_read"] == ["AGENTS.md"]
