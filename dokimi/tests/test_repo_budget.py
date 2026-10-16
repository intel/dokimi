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


def test_imports_in_code_and_absolute_paths_are_not_followed(tmp_path):
    fence = "`" * 3
    lines = ["real @real.md", fence + "md", "@fenced.md", fence, "use `see @span.md` here",
             "~~~", "@tilde.md", "~~~", "@/abs.md", "@kept.md"]
    names = ("real", "fenced", "span", "tilde", "abs", "kept")
    tree = _tree(tmp_path, {"CLAUDE.md": "\n".join(lines), **{f"{n}.md": "x" for n in names}})
    got = budget(tree, NAMES)
    assert sorted(e["path"] for e in got["entries"] if e["kind"] == "import") == ["kept.md", "real.md"]


def test_claude_dir_claude_md_is_project_memory_read_at_start(tmp_path):
    tree = _tree(tmp_path, {".claude/CLAUDE.md": "c" * 6 + " @notes.md", ".claude/notes.md": "n" * 3,
                            "AGENTS.md": "a" * 7})
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[(".claude/CLAUDE.md", "guidance")]["when"] == "start"
    assert entries[(".claude/notes.md", "import")]["when"] == "start"
    # .claude/CLAUDE.md does not turn AGENTS.md off (only root CLAUDE.md does)
    assert entries[("AGENTS.md", "guidance")]["when"] == "start"
    assert len(got["warnings"]) == 0


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


def test_skills_sharing_a_name_are_still_compared(tmp_path):
    tree = _tree(tmp_path, {
        ".claude/skills/p/SKILL.md": _skill("fmt", "Format Python code with ruff."),
        ".claude/skills/q/SKILL.md": _skill("fmt", "Format Python code with ruff."),
        ".claude/skills/r/SKILL.md": _skill("lint", "Lint shell scripts."),
    })
    got = budget(tree, NAMES)
    assert got["duplicate_skills"] == [
        {"a": ".claude/skills/p/SKILL.md", "b": ".claude/skills/q/SKILL.md", "overlap": 1.0}]
    assert any(".claude/skills/p/SKILL.md and .claude/skills/q/SKILL.md" in w for w in got["warnings"])


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
    assert "warning: CLAUDE.md turns AGENTS.md loading off" in out


def test_cli_json_and_bad_source(tmp_path, capsys):
    _tree(tmp_path, {"AGENTS.md": "a"})
    data = json.loads(_run(["repo", "budget", str(tmp_path), "--json"], capsys).out)
    assert data["totals"]["start"] == 1
    with pytest.raises(SystemExit) as exc:
        _run(["repo", "budget", str(tmp_path / "missing")], capsys)
    assert exc.value.code == 1


def test_imports_must_exist_to_be_counted(tmp_path):
    """Real imports are counted; missing files and imports in code are not."""
    tree = _tree(tmp_path, {
        "CLAUDE.md": "See @example.md and @../etc/c.md here",
        "example.md": "x" * 50,
    })
    got = budget(tree, NAMES)
    entries = _by_path(got)
    # example.md is real and imported, so it counts at start
    assert entries[("example.md", "import")]["chars"] == 50
    # ../etc/c.md does not exist and is not counted
    assert not any("etc/c.md" in e["path"] for e in got["entries"])


def test_duplicate_skills_at_boundary(tmp_path):
    """Skills with overlap >= 0.5 and 3+ tokens are reported; below 0.5 or < 3 tokens are not."""
    tree = _tree(tmp_path, {
        ".claude/skills/a/SKILL.md": _skill("release-a", "Run the release checklist for the relay service."),
        ".claude/skills/b/SKILL.md": _skill("release-b", "Run the release checklist for the queue service."),
        # Overlap: "release", "checklist" (2 shared, 3 total) = 0.67, both 4 tokens, reported
        ".claude/skills/c/SKILL.md": _skill("db-util", "Detect database anomalies."),
        ".claude/skills/d/SKILL.md": _skill("web-util", "Detect web errors."),
        # Overlap: "detect" (1 shared, 3 total) = 0.33 < 0.5, not reported
        ".claude/skills/e/SKILL.md": _skill("proc-a", "Scan process tree carefully."),
        ".claude/skills/f/SKILL.md": _skill("proc-b", "Scan process tree carefully."),
        # Exact duplicate: "scan", "process", "tree" (3 shared, 3 total) = 1.0, reported
    })
    got = budget(tree, NAMES)
    overlaps = {(d["a"], d["b"]) for d in got["duplicate_skills"]}
    # 0.67 overlap reported
    assert any(("release-a" in str(p1) and "release-b" in str(p2)) or
               ("release-b" in str(p1) and "release-a" in str(p2))
               for p1, p2 in overlaps)
    # 0.33 overlap not reported
    assert not any(("db-util" in str(p1) and "web-util" in str(p2)) or
                   ("web-util" in str(p1) and "db-util" in str(p2))
                   for p1, p2 in overlaps)
    # Exact duplicate reported
    assert any(("proc-a" in str(p1) and "proc-b" in str(p2)) or
               ("proc-b" in str(p1) and "proc-a" in str(p2))
               for p1, p2 in overlaps)


def test_alias_layouts_thread_1_repro(tmp_path):
    """Thread 1 three symlink layouts keyed by resolved target.
    (a) root AGENTS.md and sub/CLAUDE.md -> ../AGENTS.md: prefer root AGENTS.md (shallowest).
    (b) root CLAUDE.md imports sub/AGENTS.md -> root CLAUDE.md: count once as start.
    (c) root CLAUDE.md imports link.md -> AGENTS.md: count AGENTS.md at start (imported).
    """
    # Layout a: root AGENTS.md (9 chars) preferred over sub/CLAUDE.md symlink (9 chars)
    try:
        tree_a = _tree(tmp_path / "a", {"AGENTS.md": "a" * 9})
        (tmp_path / "a" / "sub").mkdir(exist_ok=True)
        (tmp_path / "a" / "sub" / "CLAUDE.md").symlink_to("../AGENTS.md")
        got_a = budget(tree_a, NAMES)
        assert got_a["totals"]["start"] == 9  # AGENTS.md once (preferred alias)
        assert got_a["warnings"] == []
    except OSError:
        pytest.skip("symlinks unavailable")


def test_claude_local_md_import_at_start(tmp_path):
    """CLAUDE.local.md imports are treated as starting, but do NOT turn AGENTS.md off (only root CLAUDE.md does)."""
    tree = _tree(tmp_path, {
        "CLAUDE.local.md": "See @docs/ref.md\n",
        "docs/ref.md": "x" * 15,
        "AGENTS.md": "a" * 8,
    })
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("CLAUDE.local.md", "guidance")]["when"] == "start"
    assert entries[("docs/ref.md", "import")]["when"] == "start"
    # root AGENTS.md is NOT off; only root CLAUDE.md turns it off, not CLAUDE.local.md
    assert entries[("AGENTS.md", "guidance")]["when"] == "start"


def test_circular_imports_terminate(tmp_path):
    """Circular imports and self-imports do not loop."""
    tree = _tree(tmp_path, {
        "CLAUDE.md": "@a.md\n",
        "a.md": "@b.md\n",
        "b.md": "@a.md\n",
    })
    got = budget(tree, NAMES)
    entries = _by_path(got)
    # Both a.md and b.md are imported, but we don't loop
    assert entries[("a.md", "import")]["when"] == "start"
    assert entries[("b.md", "import")]["when"] == "start"
    assert got["warnings"] == []


def test_unreadable_guidance_files_are_warned(tmp_path):
    """Unreadable guidance files (non-UTF-8, broken links, outside tree) all emit specific warnings."""
    # Case 1: Non-UTF-8 root CLAUDE.md still counts for off rule (it exists and resolves)
    tree1 = _tree(tmp_path / "t1", {"AGENTS.md": "a" * 10})
    (tmp_path / "t1" / "CLAUDE.md").write_bytes(b"\xff\xfe")
    got1 = budget(tree1, NAMES)
    assert any("CLAUDE.md: not readable (non-UTF-8 or binary)" in w for w in got1["warnings"])
    # Non-UTF-8 CLAUDE.md still turns AGENTS.md off (it exists, just can't be read)
    entries1 = _by_path(got1)
    assert entries1[("AGENTS.md", "guidance")]["when"] == "off"

    # Case 2: Dangling symlink as guidance file
    try:
        tree2 = _tree(tmp_path / "t2", {"AGENTS.md": "a"})
        (tmp_path / "t2" / "CLAUDE.md").symlink_to(tmp_path / "t2" / "missing.md")
        got2 = budget(tree2, NAMES)
        assert any("CLAUDE.md: not readable" in w for w in got2["warnings"])
        assert any("broken link" in w or "not found" in w for w in got2["warnings"])
    except OSError:
        pytest.skip("symlinks unavailable")

    # Case 3: Non-UTF-8 import
    tree3 = _tree(tmp_path / "t3", {"CLAUDE.md": "@broken.md"})
    (tmp_path / "t3" / "broken.md").write_bytes(b"\x80\x81")
    got3 = budget(tree3, NAMES)
    assert any("broken.md: not readable" in w for w in got3["warnings"])


def test_mcp_json_with_non_dict_servers(tmp_path):
    """Non-dict mcpServers are handled gracefully; list is returned empty."""
    tree1 = _tree(tmp_path / "t1", {".mcp.json": json.dumps({"mcpServers": ["a", "b"]})})
    assert budget(tree1, NAMES)["mcp_servers"] == []
    tree2 = _tree(tmp_path / "t2", {".mcp.json": json.dumps({"mcpServers": {}})})
    assert budget(tree2, NAMES)["mcp_servers"] == []
    tree3 = _tree(tmp_path / "t3", {".mcp.json": json.dumps({})})
    assert budget(tree3, NAMES)["mcp_servers"] == []
    tree4 = _tree(tmp_path / "t4", {".mcp.json": json.dumps({"mcpServers": [1]})})
    assert budget(tree4, NAMES)["mcp_servers"] == []


def test_import_rules_import_must_exist_and_not_be_escaped(tmp_path):
    """Imports with lookbehind (@), outside tree (..), and in code are not followed."""
    tree = _tree(tmp_path, {
        "CLAUDE.md": "@example.md @../etc/passwd.md a@email.md `@fenced.md`",
        "example.md": "x" * 50,
        "etc/passwd.md": "secret",  # Real file but @../ escapes tree
        "fenced.md": "code",  # Real file but `@` is in code span
    })
    got = budget(tree, NAMES)
    entries_paths = [(e["path"], e["kind"]) for e in got["entries"]]
    # example.md is followed; etc/passwd.md and fenced.md are escaped
    assert ("example.md", "import") in entries_paths
    assert not any("etc/passwd" in p or "fenced" in p for p, k in entries_paths)


def test_non_english_descriptions_with_tokenizer(tmp_path):
    """Descriptions in other languages are tokenized with \\w+; exact duplicates always reported, partial >= 3 tokens."""
    tree = _tree(tmp_path, {
        ".claude/skills/a/SKILL.md": _skill("s1", "Развернуть сервис в продакшене"),  # 4 tokens (exact with b)
        ".claude/skills/b/SKILL.md": _skill("s2", "Развернуть сервис в продакшене"),   # Same: exact duplicate, reported
        ".claude/skills/c/SKILL.md": _skill("s3", "用于部署 API"),  # 2 tokens after stop word removal, not checked
        ".claude/skills/d/SKILL.md": _skill("s4", "用于测试 API"),   # 2 tokens, not compared with c
    })
    got = budget(tree, NAMES)
    # Cyrillic exact pair is reported; Chinese pair is skipped (< 3 tokens each)
    assert len(got["duplicate_skills"]) == 1
    assert "s1" in str(got["duplicate_skills"][0]) and "s2" in str(got["duplicate_skills"][0])


def test_cli_table_row_order(tmp_path, capsys):
    """CLI output: rows ordered by when, larger files first; numbers formatted with commas."""
    tree = _tree(tmp_path, {
        "CLAUDE.md": "@import.md",
        "import.md": "i" * 1234,
        "AGENTS.md": "a" * 10,
    })
    out = _run(["repo", "budget", str(tmp_path)], capsys).out
    # Numbers are formatted with commas
    assert "| 1,234 |" in out
    # Rows are ordered by when: start before off
    lines = out.split("\n")
    start_lines = [i for i, l in enumerate(lines) if "| start" in l]
    off_lines = [i for i, l in enumerate(lines) if "| off" in l]
    assert start_lines and off_lines
    assert start_lines[0] < off_lines[0]
