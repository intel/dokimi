# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget` and `.claude/rules`: when each rule loads, and what of it is counted."""

from __future__ import annotations

import pytest

from dokimi.repos.budget import budget
from dokimi.repos.source import WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


def _tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return WorkTree(tmp_path)


def _by_path(payload):
    return {(e["path"], e["kind"]): e for e in payload["entries"]}


@pytest.mark.outcome("cjm-repo-budget")
def test_rules_start_at_the_root_unless_scoped_and_lazily_in_a_subdirectory(tmp_path):
    # Measured on Claude Code 2.1.304: a root rule raised the first prompt by its size; a scoped one,
    # a nested one and a rule's @path did not (scratch probe, haiku, first-call prompt tokens).
    scoped = "---\npaths:\n  - \"src/**\"\n---\nscoped body\n"
    payload = budget(_tree(tmp_path, {
        ".claude/rules/plain.md": "plain body\n",
        ".claude/rules/deep/x.md": "deep body\n",
        ".claude/rules/scoped.md": scoped,
        ".claude/rules/bad.md": "---\npaths: [unclosed\n---\nbad yaml body\n",
        ".claude/rules/imp.md": "See @extra.md\n",
        "extra.md": "never loaded\n",
        ".claude/rules/notes.txt": "not a rule\n",
        "sub/.claude/rules/n.md": "nested body\n",
    }), NAMES, rules=True)
    got = _by_path(payload)
    rules = {p: (e["when"], e["chars"]) for (p, k), e in got.items() if k == "rule"}
    assert rules == {
        ".claude/rules/plain.md": ("start", len("plain body\n")),
        ".claude/rules/deep/x.md": ("start", len("deep body\n")),
        ".claude/rules/imp.md": ("start", len("See @extra.md\n")),
        # the frontmatter is removed before the rule loads; `paths` holds it back even unparsed
        ".claude/rules/scoped.md": ("lazy", len("scoped body\n")),
        ".claude/rules/bad.md": ("lazy", len("bad yaml body\n")),
        "sub/.claude/rules/n.md": ("lazy", len("nested body\n")),
    }
    assert ("extra.md", "import") not in got
    assert payload["totals"]["start"] == len("plain body\n") + len("deep body\n") + len("See @extra.md\n")
    assert payload["warnings"] == []


def test_a_rule_does_not_turn_agents_md_off(tmp_path):
    got = _by_path(budget(_tree(tmp_path, {"AGENTS.md": "root\n", ".claude/rules/r.md": "rule\n"}), NAMES, rules=True))
    assert got[("AGENTS.md", "guidance")]["when"] == "start"
    assert got[(".claude/rules/r.md", "rule")]["when"] == "start"


def test_a_rule_a_root_claude_md_imports_is_one_start_entry(tmp_path):
    got = _by_path(budget(_tree(tmp_path, {
        "CLAUDE.md": "@.claude/rules/api.md\n",
        ".claude/rules/api.md": "---\npaths: src/**\n---\napi\n",
    }), NAMES, rules=True))
    assert got[(".claude/rules/api.md", "rule")]["when"] == "start"
    assert (".claude/rules/api.md", "import") not in got


def test_an_unreadable_rule_is_counted_as_zero_and_named(tmp_path):
    (tmp_path / ".claude/rules").mkdir(parents=True)
    (tmp_path / ".claude/rules/bin.md").write_bytes(b"\xff\xfe\x00")
    payload = budget(WorkTree(tmp_path), NAMES, rules=True)
    assert _by_path(payload)[(".claude/rules/bin.md", "rule")]["chars"] == 0
    assert payload["warnings"] == [".claude/rules/bin.md: not readable (non-UTF-8 or binary), counted as 0"]


def test_rules_are_counted_only_for_a_harness_that_reads_them(tmp_path):
    from dokimi.repos.harness_profile import load_profile

    tree = _tree(tmp_path, {"AGENTS.md": "root\n", ".claude/rules/r.md": "rule\n"})
    for name in ("codex", "copilot"):
        profile = load_profile(name)
        assert not profile.reads_claude_rules
        got = budget(tree, profile.guidance_names, profile.import_max_depth, profile.reads_claude_rules)
        assert not [e for e in got["entries"] if e["kind"] == "rule"], name
    assert load_profile("claude-code").reads_claude_rules
