# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill/rule package detection across agent ecosystems (``dokimi.skills.formats``).

The fixtures copy the shapes of public samples: openai/skills (``agents/openai.yaml``),
google-gemini/gemini-cli (``gemini-extension.json``, ``.gemini/skills``),
PatrickJS/awesome-cursorrules (``.mdc`` with ``globs: **/*``), github/awesome-copilot
(``*.instructions.md`` with ``applyTo``), anthropics/claude-plugins-official
(``.claude-plugin/plugin.json``) and benchflow-ai/skillsbench (``environment/skills``).
"""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.cli import build_parser
from dokimi.skills import discover_skills
from dokimi.skills import formats as sf


def _w(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _skill(d: Path, name: str, desc: str = "Use when profiling.") -> Path:
    return _w(d / "SKILL.md", f"---\nname: {name}\ndescription: {desc}\n---\nbody\n")


def _tree(root: Path) -> Path:
    _skill(root / ".codex/skills/imagegen", "imagegen")
    _w(root / ".codex/skills/imagegen/agents/openai.yaml", "interface:\n  display_name: Image Gen\n")
    _skill(root / ".gemini/skills/code_explorer", "code_explorer")
    _skill(root / "tasks/t1/environment/skills/xlsx", "xlsx", "x" * 1100)
    _w(root / ".cursor/rules/ts.mdc",
       "---\ndescription: \"TypeScript rules\"\nglobs: **/*.ts\nalwaysApply: false\n---\n# TS\n")
    _w(root / ".cursor/rules/always.mdc", "---\nalwaysApply: true\n---\nbe terse\n")
    _w(root / ".github/instructions/swift.instructions.md",
       "---\ndescription: 'Swift MCP'\napplyTo: \"**/*.swift\"\n---\n# Swift\n")
    _w(root / ".github/copilot-instructions.md", "Repo-wide rules\n")
    ext = root / "ext/policy"
    _w(ext / "gemini-extension.json",
       json.dumps({"name": "policy", "version": "1", "mcpServers": {"s": {}}}))
    _w(ext / "GEMINI.md", "context\n")
    _skill(ext / "skills/audit", "audit")
    plugin = root / "plugins/hookify"
    _w(plugin / ".claude-plugin/plugin.json", json.dumps({"name": "hookify", "description": "d"}))
    _skill(plugin / "skills/writing-rules", "writing-hookify-rules")
    _w(root / "node_modules/x/SKILL.md", "---\nname: x\ndescription: d\n---\n")
    return root


def test_each_ecosystem_is_named_with_how_it_is_selected(tmp_path):
    root = _tree(tmp_path)
    by_path = {p.path.relative_to(root).as_posix(): p for p in sf.detect_skill_packages(root)}
    assert "node_modules/x/SKILL.md" not in by_path
    codex = by_path[".codex/skills/imagegen/SKILL.md"]
    assert (codex.format, codex.host, codex.loadable, codex.problems) == (
        "agent-skill", "codex", True, [])
    assert codex.details == {"codex_metadata": "agents/openai.yaml"}
    assert by_path[".gemini/skills/code_explorer/SKILL.md"].problems[0].startswith(
        "name 'code_explorer' is not")
    bundled = by_path["tasks/t1/environment/skills/xlsx/SKILL.md"]
    assert bundled.host == "task-bundled" and "over 1024" in bundled.problems[0]
    ts_rule = by_path[".cursor/rules/ts.mdc"]
    assert (ts_rule.format, ts_rule.selected_by, ts_rule.description) == (
        "cursor-rule", "globs", "TypeScript rules")  # YAML refuses `**/*.ts`; read anyway
    assert by_path[".cursor/rules/always.mdc"].selected_by == "always"
    assert by_path[".github/instructions/swift.instructions.md"].details == {"applyTo": "**/*.swift"}
    assert by_path[".github/copilot-instructions.md"].selected_by == "always"
    ext = by_path["ext/policy/gemini-extension.json"]
    assert (ext.selected_by, ext.details) == (
        "install", {"skills": 1, "context_file": "GEMINI.md", "mcp_servers": 1})
    plugin = by_path["plugins/hookify/.claude-plugin/plugin.json"]
    assert (plugin.format, plugin.details["skills"]) == ("claude-plugin", 1)
    assert "differs from its directory" in by_path[
        "plugins/hookify/skills/writing-rules/SKILL.md"].problems[0]


def test_every_loadable_package_is_one_the_skill_loader_loads(tmp_path):
    root = _tree(tmp_path)
    loadable = {p.name for p in sf.detect_skill_packages(root) if p.loadable}
    # The loader does not skip node_modules/ (a vendored copy loads as a skill); the scan does.
    assert loadable == {s.name for s in discover_skills(root)} - {"x"}


def test_a_malformed_manifest_is_a_problem_not_a_crash(tmp_path):
    for value in (5, True, "abc"):
        root = tmp_path / str(value)
        _w(root / "ext/gemini-extension.json", json.dumps({"name": "e", "mcpServers": value}))
        _skill(root / "s/ok", "ok")
        by_format = {p.format: p for p in sf.detect_skill_packages(root)}
        ext = by_format["gemini-extension"]
        assert ext.problems == ["mcpServers is not an object"] and "mcp_servers" not in ext.details
        assert by_format["agent-skill"].name == "ok"

def test_spec_lint_rules():
    f = Path("a-b/SKILL.md")
    assert sf.lint_agent_skill({"name": "a-b", "description": "d"}, f) == []
    for name in ("A-b", "-ab", "a--b", "a" * 65):
        assert "is not" in sf.lint_agent_skill({"name": name, "description": "d"}, Path(f"{name}/SKILL.md"))[0]
    assert sf.lint_agent_skill({}, f) == ["no name", "no description"]


def test_catalog_formats_cli(tmp_path, capsys):
    root = _tree(tmp_path / "r")
    args = build_parser().parse_args(["catalog", "formats", "--root", str(root), "--json"])
    args.func(args)
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["agent-skill"]["n"] == 5
    assert data["summary"]["cursor-rule"]["selected_by"] == {"globs": 1, "always": 1}
    args = build_parser().parse_args(["catalog", "formats", "--root", str(root)])
    args.func(args)
    assert "# gemini-extension: 1" in capsys.readouterr().out


def test_catalog_formats_counts_loadable_skills_as_discover_skills(tmp_path, capsys):
    _skill(tmp_path / "template", "template-skill")
    for host in (".agents", ".claude"):
        _skill(tmp_path / host / "skills/bar", "bar")
    args = build_parser().parse_args(["catalog", "formats", "--root", str(tmp_path), "--json"])
    args.func(args)
    row = json.loads(capsys.readouterr().out)["summary"]["agent-skill"]
    assert (row["n"], row["loadable"]) == (3, len(discover_skills(tmp_path))) == (3, 1)


def test_catalog_formats_survives_a_skill_md_that_is_not_utf8(tmp_path, capsys):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_bytes(b"---\nname: bad\ndescription: caf\xe9\n---\nbody\n")
    args = build_parser().parse_args(["catalog", "formats", "--root", str(tmp_path), "--json"])
    args.func(args)
    assert json.loads(capsys.readouterr().out)["summary"]["agent-skill"]["n"] == 1
