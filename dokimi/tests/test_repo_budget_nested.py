# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: a nested AGENTS.md turned off by a CLAUDE* file in its own directory (Claude Code 2.1.316)."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.repos.budget import budget
from dokimi.repos.source import WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


def _tree(tmp_path, files, links=None):
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    for rel, target in (links or {}).items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        try:
            (tmp_path / rel).symlink_to(target)
        except OSError:
            pytest.skip("symlinks unavailable")
    return WorkTree(tmp_path)


def _when(got):
    return {e["path"]: e["when"] for e in got["entries"]}


def _budget(tree, names=NAMES):
    """As `repo budget --harness claude-code` calls it: the profile sets guidance.claude_displaces_agents."""
    return budget(tree, names, claude_displaces_agents=True)


def _cli_when(tmp_path, capsys, harness):
    args = build_parser().parse_args(["repo", "budget", str(tmp_path), "--json", "--harness", harness])
    args.func(args)
    return _when(json.loads(capsys.readouterr().out))


@pytest.mark.parametrize("harness", ["copilot", "copilot-review"])
def test_a_copilot_budget_keeps_a_nested_agents_md_beside_a_claude_md_lazy(tmp_path, capsys, harness):
    """Copilot's guidance names hold CLAUDE.md, but the same-directory rule is Claude Code's: not applied."""
    _tree(tmp_path, {"AGENTS.md": "a", "sub/AGENTS.md": "b" * 7, "sub/CLAUDE.md": "c"})
    assert _cli_when(tmp_path, capsys, harness) == {
        "AGENTS.md": "start", "sub/AGENTS.md": "lazy", "sub/CLAUDE.md": "lazy"}


def test_the_claude_code_budget_turns_a_nested_agents_md_beside_a_claude_md_off(tmp_path, capsys):
    _tree(tmp_path, {"AGENTS.md": "a", "sub/AGENTS.md": "b" * 7, "sub/CLAUDE.md": "c"})
    assert _cli_when(tmp_path, capsys, "claude-code")["sub/AGENTS.md"] == "off"


def test_without_the_profile_flag_a_nested_agents_md_beside_a_claude_md_stays_lazy(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a", "sub/AGENTS.md": "b" * 7, "sub/CLAUDE.md": "c"})
    got = budget(tree, NAMES)
    assert _when(got)["sub/AGENTS.md"] == "lazy"
    assert got["warnings"] == []


@pytest.mark.parametrize("claude", ["sub/CLAUDE.md", "sub/CLAUDE.local.md", "sub/.claude/CLAUDE.md"])
def test_a_claude_file_in_its_own_directory_turns_a_nested_agents_md_off(tmp_path, claude):
    tree = _tree(tmp_path, {"AGENTS.md": "a" * 10, "sub/AGENTS.md": "b" * 7, claude: "c" * 3})
    got = _budget(tree)
    assert _when(got) == {"AGENTS.md": "start", "sub/AGENTS.md": "off", claude: "lazy"}
    assert got["totals"] == {"start": 10, "lazy": 3, "on demand": 0, "off": 7}
    assert got["warnings"] == [
        f"{claude} turns AGENTS.md loading off in sub/ and no CLAUDE.md imports sub/AGENTS.md, so Claude Code does "
        "not read it unless the agent opens it (import it with `@AGENTS.md` from a CLAUDE.md beside it; "
        "`repo reach` measures it)"]


@pytest.mark.parametrize(("claude", "agents"), [
    ("sub/deep/CLAUDE.md", "sub/AGENTS.md"),  # a deeper CLAUDE.md
    ("sub/CLAUDE.md", "sub/deep/AGENTS.md"),  # a parent's CLAUDE.md
    ("other/CLAUDE.md", "sub/AGENTS.md"),  # a sibling's
])
def test_a_claude_file_in_another_directory_leaves_a_nested_agents_md_lazy(tmp_path, claude, agents):
    tree = _tree(tmp_path, {"AGENTS.md": "a", agents: "b", claude: "c"})
    got = _budget(tree)
    assert _when(got)[agents] == "lazy"
    assert got["warnings"] == []


@pytest.mark.parametrize("claude", [".claude/CLAUDE.local.md", ".claude/.claude/CLAUDE.md"])
def test_a_claude_file_beside_the_project_memory_agents_md_leaves_it_at_start(tmp_path, claude):
    """.claude/AGENTS.md is read at start, as a root file: the nested rule does not apply inside .claude/."""
    tree = _tree(tmp_path, {".claude/AGENTS.md": "a" * 5, claude: "c"})
    got = _budget(tree)
    assert _when(got) == {".claude/AGENTS.md": "start", claude: "lazy"}
    assert got["totals"]["off"] == 0
    assert got["warnings"] == []


def test_a_harness_without_claude_names_keeps_a_nested_agents_md_lazy(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a", "sub/AGENTS.md": "b", "sub/CLAUDE.md": "c"})
    assert _when(_budget(tree, ("AGENTS.md",))) == {"AGENTS.md": "start", "sub/AGENTS.md": "lazy"}


def test_a_dangling_claude_link_beside_a_nested_agents_md_turns_nothing_off(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "b"}, {"sub/CLAUDE.md": "missing.md"})
    assert _when(_budget(tree))["sub/AGENTS.md"] == "lazy"


def test_a_displaced_nested_agents_md_adds_no_imports(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "@notes.md\n", "sub/notes.md": "n" * 4, "sub/CLAUDE.md": "c"})
    got = _budget(tree)
    assert _when(got) == {"sub/AGENTS.md": "off", "sub/CLAUDE.md": "lazy"}
    assert got["totals"]["lazy"] == 1


def test_a_nested_agents_md_its_claude_md_imports_stays_lazy_with_its_imports(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "@notes.md\n", "sub/notes.md": "n" * 4, "sub/CLAUDE.md": "@AGENTS.md\n"})
    got = _budget(tree)
    assert _when(got) == {"sub/AGENTS.md": "lazy", "sub/CLAUDE.md": "lazy", "sub/notes.md": "lazy"}
    assert got["warnings"] == []


def test_a_nested_agents_md_its_claude_file_links_to_stays_lazy_with_its_imports(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "@notes.md\n", "sub/notes.md": "n" * 4},
                 {"sub/.claude/CLAUDE.md": "../AGENTS.md"})
    got = _budget(tree)
    assert _when(got)["sub/AGENTS.md"] == "lazy"
    assert _when(got)["sub/notes.md"] == "lazy"  # read as AGENTS.md from sub/, as the root rule does
    assert got["warnings"] == []


def test_a_nested_agents_md_another_agents_md_links_to_is_not_off(tmp_path):
    """sub/AGENTS.md is displaced, but the same file is read as zzz/AGENTS.md, where no CLAUDE* is."""
    tree = _tree(tmp_path, {"sub/AGENTS.md": "b" * 5, "sub/CLAUDE.md": "c"}, {"zzz/AGENTS.md": "../sub/AGENTS.md"})
    got = _budget(tree)
    assert _when(got) == {"sub/AGENTS.md": "lazy", "sub/CLAUDE.md": "lazy"}
    assert got["totals"]["off"] == 0
