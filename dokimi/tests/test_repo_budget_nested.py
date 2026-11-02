# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: a nested AGENTS.md turned off by a CLAUDE* file in its own directory (Claude Code 2.1.316)."""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize("claude", ["sub/CLAUDE.md", "sub/CLAUDE.local.md", "sub/.claude/CLAUDE.md"])
def test_a_claude_file_in_its_own_directory_turns_a_nested_agents_md_off(tmp_path, claude):
    tree = _tree(tmp_path, {"AGENTS.md": "a" * 10, "sub/AGENTS.md": "b" * 7, claude: "c" * 3})
    got = budget(tree, NAMES)
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
    got = budget(tree, NAMES)
    assert _when(got)[agents] == "lazy"
    assert got["warnings"] == []


def test_a_harness_without_claude_names_keeps_a_nested_agents_md_lazy(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a", "sub/AGENTS.md": "b", "sub/CLAUDE.md": "c"})
    assert _when(budget(tree, ("AGENTS.md",))) == {"AGENTS.md": "start", "sub/AGENTS.md": "lazy"}


def test_a_dangling_claude_link_beside_a_nested_agents_md_turns_nothing_off(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "b"}, {"sub/CLAUDE.md": "missing.md"})
    assert _when(budget(tree, NAMES))["sub/AGENTS.md"] == "lazy"


def test_a_displaced_nested_agents_md_adds_no_imports(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "@notes.md\n", "sub/notes.md": "n" * 4, "sub/CLAUDE.md": "c"})
    got = budget(tree, NAMES)
    assert _when(got) == {"sub/AGENTS.md": "off", "sub/CLAUDE.md": "lazy"}
    assert got["totals"]["lazy"] == 1


def test_a_nested_agents_md_its_claude_md_imports_stays_lazy_with_its_imports(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "@notes.md\n", "sub/notes.md": "n" * 4, "sub/CLAUDE.md": "@AGENTS.md\n"})
    got = budget(tree, NAMES)
    assert _when(got) == {"sub/AGENTS.md": "lazy", "sub/CLAUDE.md": "lazy", "sub/notes.md": "lazy"}
    assert got["warnings"] == []


def test_a_nested_agents_md_its_claude_file_links_to_stays_lazy_with_its_imports(tmp_path):
    tree = _tree(tmp_path, {"sub/AGENTS.md": "@notes.md\n", "sub/notes.md": "n" * 4},
                 {"sub/.claude/CLAUDE.md": "../AGENTS.md"})
    got = budget(tree, NAMES)
    assert _when(got)["sub/AGENTS.md"] == "lazy"
    assert _when(got)["sub/notes.md"] == "lazy"  # read as AGENTS.md from sub/, as the root rule does
    assert got["warnings"] == []


def test_a_nested_agents_md_another_agents_md_links_to_is_not_off(tmp_path):
    """sub/AGENTS.md is displaced, but the same file is read as zzz/AGENTS.md, where no CLAUDE* is."""
    tree = _tree(tmp_path, {"sub/AGENTS.md": "b" * 5, "sub/CLAUDE.md": "c"}, {"zzz/AGENTS.md": "../sub/AGENTS.md"})
    got = budget(tree, NAMES)
    assert _when(got) == {"sub/AGENTS.md": "lazy", "sub/CLAUDE.md": "lazy"}
    assert got["totals"]["off"] == 0
