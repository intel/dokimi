# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The instruction-file rules of ``repo inspect``: which harness reads each file, and where it applies."""

from __future__ import annotations

from dokimi.repos.environment.instructions import detect_instructions

INSTRUCTION_TABLE = [
    (".claude/CLAUDE.md", ('claude-code',), "root", "always", False),
    (".claude/rules/a.md", ('claude-code',), "unknown", "unknown", False),
    (".claude/skills/x/SKILL.md", ('claude-code',), "capability", "on-demand", False),
    (".clinerules", ('cline',), "legacy", "always", True),
    (".clinerules/a.md", ('cline',), "root", "unknown", False),
    (".cursor/rules/a.mdc", ('cursor',), "unknown", "unknown", False),
    (".cursorrules", ('cursor', 'cline'), "legacy", "always", True),
    (".github/agents/a.agent.md", ('github-copilot',), "capability", "manual", False),
    (".github/copilot-instructions.md", ('github-copilot',), "root", "always", False),
    (".github/instructions/a.instructions.md", ('github-copilot',), "path-specific", "path-scoped", False),
    (".roo/rules-code/a.md", ('roo-code',), "mode-specific", "mode-scoped", False),
    (".roo/rules/a.md", ('roo-code',), "root", "always", False),
    (".roomodes", ('roo-code',), "mode-specific", "mode-scoped", False),
    (".roorules", ('roo-code',), "legacy", "always", True),
    (".roorules-code", ('roo-code',), "mode-specific", "mode-scoped", True),
    (".windsurf/rules/a.md", ('windsurf',), "unknown", "unknown", False),
    (".windsurfrules", ('windsurf', 'cline'), "legacy", "always", True),
    ("AGENTS.md", ('codex', 'github-copilot', 'cursor', 'windsurf', 'cline', 'generic'), "root", "always", False),
    ("AGENTS.override.md", ('codex',), "root", "always", False),
    ("CLAUDE.local.md", ('claude-code',), "local-private", "always", False),
    ("CLAUDE.md", ('claude-code', 'github-copilot'), "root", "always", False),
    ("GEMINI.md", ('gemini', 'github-copilot'), "root", "always", False),
    ("sub/.cursor/rules/a.mdc", ('cursor',), "unknown", "unknown", False),
    ("sub/AGENTS.md", ('codex', 'github-copilot', 'cursor', 'windsurf', 'cline', 'generic'), "path-specific", "path-scoped", False),
    ("sub/AGENTS.override.md", ('codex',), "path-specific", "path-scoped", False),
    ("sub/CLAUDE.md", ('claude-code',), "path-specific", "path-scoped", False),
    ("sub/GEMINI.md", ('gemini',), "path-specific", "path-scoped", False),
]


def test_instruction_rule_table():
    got = [(i.path, tuple(i.harnesses), i.scope, i.activation, i.legacy)
           for i in detect_instructions([row[0] for row in INSTRUCTION_TABLE])]
    assert got == INSTRUCTION_TABLE


def test_a_nested_cursor_rule_is_listed_with_its_directory_and_no_known_scope():
    (rule,) = detect_instructions(["pkg/web/.cursor/rules/style.mdc", "pkg/web/.cursor/notes.md"])
    assert (rule.path, rule.scope, rule.directory) == ("pkg/web/.cursor/rules/style.mdc", "unknown", "pkg/web")
    assert any("Cursor documents only the root .cursor/rules" in note for note in rule.notes)
