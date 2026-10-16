# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: what a repository puts into an agent's context, from the tree alone."""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.repos.budget import budget
from dokimi.repos.source import WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


def _skill(name, description, body="Do the thing.\n"):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def _tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return WorkTree(tmp_path)


def _by_path(payload):
    return {(e["path"], e["kind"]): e for e in payload["entries"]}


def test_root_guidance_starts_nested_is_lazy(tmp_path):
    tree = _tree(tmp_path, {"AGENTS.md": "a" * 100, "src/AGENTS.md": "b" * 40, "README.md": "x"})
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "start"
    assert entries[("src/AGENTS.md", "guidance")]["when"] == "lazy"
    assert got["totals"] == {"start": 100, "lazy": 40, "on demand": 0, "off": 0}
    assert got["warnings"] == []


def test_a_root_claude_md_turns_agents_md_off(tmp_path):
    tree = _tree(tmp_path, {"CLAUDE.md": "rules\n", "AGENTS.md": "a" * 10, "sub/AGENTS.md": "b" * 5})
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "off"
    assert entries[("sub/AGENTS.md", "guidance")]["when"] == "off"
    assert got["totals"]["off"] == 15
    assert len(got["warnings"]) == 1 and "reads none of AGENTS.md, sub/AGENTS.md unless" in got["warnings"][0]


def test_imports_load_at_start_transitively(tmp_path):
    tree = _tree(tmp_path, {
        "CLAUDE.md": "See @AGENTS.md and @docs/style.md, not `@ignored.md` or me@example.md\n",
        "AGENTS.md": "a" * 10,
        "docs/style.md": "@../docs/deep.md\n",
        "docs/deep.md": "d" * 7,
        "sub/AGENTS.md": "b" * 5,
    })
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "start"  # imported by the bridge
    assert entries[("docs/style.md", "import")]["when"] == "start"
    assert entries[("docs/deep.md", "import")]["chars"] == 7
    assert entries[("sub/AGENTS.md", "guidance")]["when"] == "off"  # not imported
    assert ("ignored.md", "import") not in entries and ("example.md", "import") not in entries


def test_a_nested_claude_md_bridge_loads_its_agents_md_lazily(tmp_path):
    tree = _tree(tmp_path, {
        "CLAUDE.md": "@AGENTS.md\n", "AGENTS.md": "a",
        "lib/CLAUDE.md": "@AGENTS.md\n@notes.md\n", "lib/AGENTS.md": "b" * 3, "lib/notes.md": "n" * 4,
        "docs/AGENTS.md": "c" * 5,
    })
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("lib/AGENTS.md", "guidance")]["when"] == "lazy"
    assert entries[("lib/notes.md", "import")]["when"] == "lazy"
    assert entries[("docs/AGENTS.md", "guidance")]["when"] == "off"  # no CLAUDE.md imports it
    assert "reads none of docs/AGENTS.md" in got["warnings"][0]


def test_an_import_outside_the_tree_is_ignored(tmp_path):
    tree = _tree(tmp_path, {"CLAUDE.md": "@../../etc/passwd.md\n"})
    assert [e["path"] for e in budget(tree, NAMES)["entries"]] == ["CLAUDE.md"]


def test_an_import_through_a_link_out_of_the_tree_is_skipped(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        (repo / "notes.md").symlink_to(outside)
        (repo / ".mcp.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    tree = _tree(repo, {"CLAUDE.md": "see @notes.md"})
    got = budget(tree, NAMES)
    assert [e["path"] for e in got["entries"]] == ["CLAUDE.md"]
    assert got["mcp_servers"] == []


def test_a_link_to_a_loaded_file_is_not_counted_again(tmp_path):
    _tree(tmp_path, {"CLAUDE.md": "c" * 20 + " @docs/same.md @docs/again.md", "docs/real.md": "r" * 5, "sub/CLAUDE.md": "s" * 4})
    try:
        (tmp_path / "AGENTS.md").symlink_to("CLAUDE.md")
        (tmp_path / "sub" / "AGENTS.md").symlink_to("CLAUDE.md")
        (tmp_path / "docs" / "same.md").symlink_to("real.md")
        (tmp_path / "docs" / "again.md").symlink_to("real.md")
    except OSError:
        pytest.skip("symlinks unavailable")
    got = budget(WorkTree(tmp_path), NAMES)
    paths = sorted(e["path"] for e in got["entries"])
    assert paths[0] == "CLAUDE.md" and paths[-1] == "sub/CLAUDE.md" and len(paths) == 3  # one of the two links
    assert got["totals"]["start"] == 20 + len(" @docs/same.md @docs/again.md") + 5
    assert got["totals"]["off"] == 0 and got["warnings"] == []


def test_skill_listing_starts_body_on_demand(tmp_path):
    tree = _tree(tmp_path, {".claude/skills/fmt/SKILL.md": _skill("fmt", "Format code.", "x" * 50)})
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[(".claude/skills/fmt/SKILL.md", "skill listing")] == {
        "path": ".claude/skills/fmt/SKILL.md", "kind": "skill listing", "when": "start", "chars": len("fmt") + len("Format code.")}
    assert entries[(".claude/skills/fmt/SKILL.md", "skill body")]["when"] == "on demand"
    assert got["skills"] == 1


def test_near_duplicate_and_missing_descriptions_warn(tmp_path):
    tree = _tree(tmp_path, {
        ".claude/skills/a/SKILL.md": _skill("release-a", "Run the release checklist for the relay service."),
        ".claude/skills/b/SKILL.md": _skill("release-b", "Run the release checklist for the queue service."),
        ".claude/skills/c/SKILL.md": _skill("lint", "Lint Python files with ruff."),
        ".claude/skills/d/SKILL.md": "no frontmatter at all\n",
    })
    got = budget(tree, NAMES)
    assert got["duplicate_skills"] == [{"a": "release-a", "b": "release-b", "overlap": 0.67}]
    assert any("near-duplicate" in w for w in got["warnings"])
    assert any(w.startswith(".claude/skills/d/SKILL.md: no description") for w in got["warnings"])
    names = {e["path"] for e in got["entries"]}
    assert ".claude/skills/d/SKILL.md" in names  # still counted, under its directory name


def test_mcp_servers_are_listed(tmp_path):
    tree = _tree(tmp_path, {".mcp.json": json.dumps({"mcpServers": {"docs": {}, "db": {}}})})
    assert budget(tree, NAMES)["mcp_servers"] == ["db", "docs"]
    bad = _tree(tmp_path / "bad", {".mcp.json": "[not json"})
    assert budget(bad, NAMES)["mcp_servers"] == []


def _run(argv, capsys):
    args = build_parser().parse_args(argv)
    args.func(args)
    return capsys.readouterr()


def test_cli_table_and_gate(tmp_path, capsys):
    _tree(tmp_path, {"CLAUDE.md": "c" * 20, "AGENTS.md": "a" * 10, ".mcp.json": '{"mcpServers": {"docs": {}}}'})
    with pytest.raises(SystemExit) as exc:
        _run(["repo", "budget", str(tmp_path), "--fail-on-warning"], capsys)
    assert exc.value.code == 1
    out = _run(["repo", "budget", str(tmp_path)], capsys).out
    assert "start 20 chars, lazy 0, on demand 0, off 10" in out
    assert "| start | guidance | CLAUDE.md | 20 |" in out
    assert "| off | guidance | AGENTS.md | 10 |" in out
    assert "MCP servers in .mcp.json: docs" in out
    assert "warning: CLAUDE.md at the root turns AGENTS.md loading off" in out


def test_cli_json_and_bad_source(tmp_path, capsys):
    _tree(tmp_path, {"AGENTS.md": "a"})
    data = json.loads(_run(["repo", "budget", str(tmp_path), "--json"], capsys).out)
    assert data["totals"]["start"] == 1
    with pytest.raises(SystemExit) as exc:
        _run(["repo", "budget", str(tmp_path / "missing")], capsys)
    assert exc.value.code == 1
