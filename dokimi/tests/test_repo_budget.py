# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget`: what a repository puts into an agent's context, from the tree alone."""

from __future__ import annotations

import json
import subprocess

import pytest

from dokimi.cli import build_parser
from dokimi.repos.budget import budget
from dokimi.repos.source import GitTree, WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


def _skill(name, description, body="Do the thing.\n"):
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}"


def _tree(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return WorkTree(tmp_path)


def _link(path, target):
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable")


def _linked_repo(root, files, links):
    """A git repository with ``links`` (path -> link text) committed as symlinks; returns its GitTree."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    for rel, text in links.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        _link(root / rel, text)
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True,  # noqa: E731
                                    text=True).stdout.strip()
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "case")
    return GitTree(root / ".git", git("rev-parse", "HEAD"))


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
        "CLAUDE.md": "See @AGENTS.md and @docs/style.md but not `@ignored.md` or me@example.md\n",
        "AGENTS.md": "a" * 10,
        "docs/style.md": "@../docs/deep.md\n",
        "docs/deep.md": "d" * 7,
        "sub/AGENTS.md": "b" * 5,
        "ignored.md": "i", "example.md": "e",  # exist, so only the span and the `me@` rule keep them out
    })
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "start"  # imported by the bridge
    assert entries[("docs/style.md", "import")]["when"] == "start"
    assert entries[("docs/deep.md", "import")]["chars"] == 7
    assert entries[("sub/AGENTS.md", "guidance")]["when"] == "off"  # not imported
    assert ("ignored.md", "import") not in entries and ("example.md", "import") not in entries
    # positive control: the same files are imports once named plainly
    (tmp_path / "CLAUDE.md").write_text("@ignored.md @example.md @AGENTS.md\n", encoding="utf-8")
    plain = _by_path(budget(WorkTree(tmp_path), NAMES))
    assert ("ignored.md", "import") in plain and ("example.md", "import") in plain


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
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "passwd.md").write_text("secret", encoding="utf-8")  # it exists, outside the tree
    tree = _tree(tmp_path / "repo", {"CLAUDE.md": "@../etc/passwd.md @ok.md\n", "ok.md": "o"})
    assert [e["path"] for e in budget(tree, NAMES)["entries"]] == ["CLAUDE.md", "ok.md"]  # ok.md: control


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


def test_the_listing_charges_when_to_use_and_caps_it_with_the_description(tmp_path):
    tree = _tree(tmp_path, {
        ".claude/skills/a/SKILL.md": "---\nname: a\ndescription: Format code.\nwhen_to_use: On save.\n---\nb",
        ".claude/skills/b/SKILL.md": _skill("b", "x" * 3000),
        ".claude/skills/c/SKILL.md": f"---\nname: c\ndescription: {'y' * 1000}\nwhen_to_use: {'z' * 1000}\n---\nb",
    })
    entries = _by_path(budget(tree, NAMES))
    listing = {p.split("/")[2]: e["chars"] for (p, kind), e in entries.items() if kind == "skill listing"}
    assert listing == {"a": 1 + len("Format code.") + len("On save."), "b": 1 + 1536, "c": 1 + 1536}


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


def test_a_skill_the_model_cannot_invoke_is_not_listed(tmp_path):
    hidden = "---\nname: rel\ndescription: Format Python code with ruff.\ndisable-model-invocation: true\n---\nbody"
    tree = _tree(tmp_path, {
        ".claude/skills/fmt/SKILL.md": _skill("fmt", "Format Python code with ruff."),
        ".claude/skills/rel/SKILL.md": hidden,
        ".claude/skills/bare/SKILL.md": "---\nname: bare\ndisable-model-invocation: yes\n---\nb",
    })
    got = budget(tree, NAMES)
    kinds = {(e["path"], e["kind"]): e for e in got["entries"]}
    assert (".claude/skills/rel/SKILL.md", "skill listing") not in kinds
    assert (".claude/skills/bare/SKILL.md", "skill listing") not in kinds
    assert kinds[(".claude/skills/rel/SKILL.md", "skill body")]["chars"] == 4
    assert got["totals"]["start"] == len("fmt") + len("Format Python code with ruff.")
    assert got["duplicate_skills"] == [] and got["warnings"] == []


def test_mcp_servers_are_listed(tmp_path):
    tree = _tree(tmp_path, {".mcp.json": json.dumps({"mcpServers": {"docs": {}, "db": {}}})})
    assert budget(tree, NAMES)["mcp_servers"] == ["db", "docs"]
    bad = _tree(tmp_path / "bad", {".mcp.json": "[not json"})
    assert budget(bad, NAMES)["mcp_servers"] == []
    for i, text in enumerate(("[]", "null", "1")):
        assert budget(_tree(tmp_path / f"top{i}", {".mcp.json": text}), NAMES)["mcp_servers"] == []


def test_identical_two_word_descriptions_are_duplicates(tmp_path):
    tree = _tree(tmp_path, {".claude/skills/one/SKILL.md": _skill("one", "Deploy app"),
                            ".claude/skills/two/SKILL.md": _skill("two", "Deploy app"),
                            ".claude/skills/three/SKILL.md": _skill("three", "Deploy service")})
    assert budget(tree, NAMES)["duplicate_skills"] == [{"a": "one", "b": "two", "overlap": 1.0}]


def test_gate_passes_a_clean_tree(tmp_path, capsys):
    _tree(tmp_path, {"CLAUDE.md": "c" * 20, ".claude/skills/a/SKILL.md": _skill("a", "Format Python code.")})
    _run(["repo", "budget", str(tmp_path), "--fail-on-warning"], capsys)  # no SystemExit


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
        # Overlap: "release", "checklist", "relay/queue", "service" (4 shared, 6 total) = 0.67, reported
        ".claude/skills/c/SKILL.md": _skill("db-util", "Detect database anomalies."),
        ".claude/skills/d/SKILL.md": _skill("web-util", "Detect web errors."),
        # Overlap: "detect" (1 shared, 3 total) = 0.33 < 0.5, not reported
        ".claude/skills/e/SKILL.md": _skill("proc-a", "Scan process tree carefully."),
        ".claude/skills/f/SKILL.md": _skill("proc-b", "Scan process tree carefully."),
        # Exact duplicate: "scan", "process", "tree" (3 shared, 3 total) = 1.0, reported
        ".claude/skills/g/SKILL.md": _skill("half-a", "alpha beta delta"),
        ".claude/skills/h/SKILL.md": _skill("half-b", "alpha beta gamma"),
        # Overlap: "alpha", "beta" (2 shared, 4 total) = 0.5, both 3 tokens, reported
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
    # Exact 0.5 overlap reported
    assert ("half-a", "half-b") in overlaps or ("half-b", "half-a") in overlaps


def test_alias_layouts_thread_1_repro(tmp_path):
    """Thread 1 three symlink layouts keyed by resolved target."""
    # Layout a: root AGENTS.md and sub/CLAUDE.md -> ../AGENTS.md
    tree_a = _tree(tmp_path / "a", {"AGENTS.md": "a" * 9})
    (tmp_path / "a" / "sub").mkdir()
    _link(tmp_path / "a" / "sub" / "CLAUDE.md", "../AGENTS.md")
    got_a = budget(tree_a, NAMES)
    assert got_a["totals"] == {"start": 9, "lazy": 0, "on demand": 0, "off": 0}
    assert got_a["warnings"] == []

    # Layout b: root CLAUDE.md imports sub/AGENTS.md, a link to sub/CLAUDE.md
    tree_b = _tree(tmp_path / "b", {"CLAUDE.md": "@sub/AGENTS.md".ljust(15), "sub/CLAUDE.md": "c" * 7})
    _link(tmp_path / "b" / "sub" / "AGENTS.md", "CLAUDE.md")
    got_b = budget(tree_b, NAMES)
    assert got_b["totals"] == {"start": 22, "lazy": 0, "on demand": 0, "off": 0}
    assert got_b["warnings"] == []

    # Layout c: root CLAUDE.md imports link.md -> AGENTS.md
    tree_c = _tree(tmp_path / "c", {"CLAUDE.md": "@link.md".ljust(13), "AGENTS.md": "a" * 5})
    _link(tmp_path / "c" / "link.md", "AGENTS.md")
    got_c = budget(tree_c, NAMES)
    assert got_c["totals"] == {"start": 18, "lazy": 0, "on demand": 0, "off": 0}
    assert got_c["warnings"] == []


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
    """Unreadable guidance files (non-UTF-8, broken links) all emit specific warnings and count 0."""
    # Non-UTF-8 root CLAUDE.md still counts for the off rule (it exists and resolves)
    tree1 = _tree(tmp_path / "t1", {"AGENTS.md": "a" * 10})
    (tmp_path / "t1" / "CLAUDE.md").write_bytes(b"\xff\xfe")
    got1 = budget(tree1, NAMES)
    assert "CLAUDE.md: not readable (non-UTF-8 or binary), counted as 0" in got1["warnings"]
    entries1 = _by_path(got1)
    assert entries1[("CLAUDE.md", "guidance")]["chars"] == 0
    assert entries1[("AGENTS.md", "guidance")]["when"] == "off"

    # Dangling symlink as CLAUDE.md does not turn AGENTS.md off
    tree2 = _tree(tmp_path / "t2", {"AGENTS.md": "a"})
    _link(tmp_path / "t2" / "CLAUDE.md", tmp_path / "t2" / "missing.md")
    got2 = budget(tree2, NAMES)
    assert got2["warnings"] == ["CLAUDE.md: not readable (not found, broken link, or outside tree), counted as 0"]
    entries2 = _by_path(got2)
    assert entries2[("CLAUDE.md", "guidance")]["chars"] == 0
    assert entries2[("AGENTS.md", "guidance")]["when"] == "start"

    # Non-UTF-8 import
    tree3 = _tree(tmp_path / "t3", {"CLAUDE.md": "@broken.md"})
    (tmp_path / "t3" / "broken.md").write_bytes(b"\x80\x81")
    got3 = budget(tree3, NAMES)
    assert got3["warnings"] == ["broken.md: not readable (non-UTF-8 or binary), counted as 0"]
    assert _by_path(got3)[("broken.md", "import")]["chars"] == 0


def test_unreadable_skill_warns_about_encoding_not_description(tmp_path):
    tree = _tree(tmp_path, {})
    skill = tmp_path / ".claude" / "skills" / "a" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_bytes(b"\xff\xfe---\nname: a\ndescription: Do x y z\n---\nbody")
    got = budget(tree, NAMES)
    assert got["warnings"] == [".claude/skills/a/SKILL.md: not readable (non-UTF-8 or binary), body counted as 0; "
                               "the listing falls back to the directory name"]
    entries = _by_path(got)
    assert entries[(".claude/skills/a/SKILL.md", "skill body")]["chars"] == 0


def test_nested_claude_alias_of_agents_md_is_lazy_not_off(tmp_path):
    """Thread 7: sub/CLAUDE.md -> ../AGENTS.md is a lazy load of AGENTS.md; AGENTS.md is not off."""
    tree = _tree(tmp_path, {"AGENTS.md": "a" * 100, "CLAUDE.md": "root claude"})
    (tmp_path / "sub").mkdir()
    _link(tmp_path / "sub" / "CLAUDE.md", "../AGENTS.md")
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "lazy"
    assert entries[("CLAUDE.md", "guidance")]["when"] == "start"
    assert got["totals"] == {"start": 11, "lazy": 100, "on demand": 0, "off": 0}
    assert got["warnings"] == []


def test_root_claude_aliases_decide_off_from_all_paths(tmp_path):
    """Thread 7 second layout: CLAUDE.local.md -> CLAUDE.md beside AGENTS.md. CLAUDE.md is still the root CLAUDE.md."""
    tree = _tree(tmp_path, {"CLAUDE.md": "root claude", "AGENTS.md": "a" * 100})
    _link(tmp_path / "CLAUDE.local.md", "CLAUDE.md")
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "off"
    assert got["totals"] == {"start": 11, "lazy": 0, "on demand": 0, "off": 100}
    assert len(got["warnings"]) == 1 and "CLAUDE.md turns AGENTS.md loading off" in got["warnings"][0]


def test_agents_md_imported_only_by_nested_claude_is_lazy(tmp_path):
    """Thread 8: the root CLAUDE.md does not import AGENTS.md; only sub/CLAUDE.md does, so it is lazy."""
    tree = _tree(tmp_path, {"AGENTS.md": "a" * 100, "CLAUDE.md": "root claude", "sub/CLAUDE.md": "@../AGENTS.md"})
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "lazy"
    assert entries[("sub/CLAUDE.md", "guidance")]["when"] == "lazy"
    assert got["totals"] == {"start": 11, "lazy": 113, "on demand": 0, "off": 0}
    assert got["warnings"] == []


def test_link_to_a_non_guidance_file_is_keyed_by_target(tmp_path):
    """Thread 11: AGENTS.md -> docs/agents.md, imported by the root CLAUDE.md as @docs/agents.md, is start."""
    tree = _tree(tmp_path, {"CLAUDE.md": "@docs/agents.md", "docs/agents.md": "a" * 20})
    _link(tmp_path / "AGENTS.md", "docs/agents.md")
    got = budget(tree, NAMES)
    entries = _by_path(got)
    assert entries[("AGENTS.md", "guidance")]["when"] == "start"
    assert ("docs/agents.md", "import") not in entries
    assert got["totals"] == {"start": 35, "lazy": 0, "on demand": 0, "off": 0}
    assert got["warnings"] == []


def test_nested_import_of_a_link_to_a_non_guidance_file_is_lazy(tmp_path):
    """Thread 11: the lazy import names AGENTS.md, a link to docs/agents.md; the load is keyed by target."""
    tree = _tree(tmp_path, {"docs/agents.md": "a" * 100, "CLAUDE.md": "root claude", "sub/CLAUDE.md": "@../AGENTS.md"})
    _link(tmp_path / "AGENTS.md", "docs/agents.md")
    got = budget(tree, NAMES)
    assert _by_path(got)[("AGENTS.md", "guidance")]["when"] == "lazy"
    assert got["totals"] == {"start": 11, "lazy": 113, "on demand": 0, "off": 0}
    assert got["warnings"] == []


def test_dot_claude_claude_md_alias_keeps_agents_md_at_start(tmp_path):
    """.claude/CLAUDE.md -> ../AGENTS.md is project memory read at start, so AGENTS.md is not off."""
    tree = _tree(tmp_path, {"AGENTS.md": "a" * 100, "CLAUDE.md": "root claude", ".claude/x": ""})
    _link(tmp_path / ".claude" / "CLAUDE.md", "../AGENTS.md")
    got = budget(tree, NAMES)
    assert _by_path(got)[("AGENTS.md", "guidance")]["when"] == "start"
    assert got["totals"] == {"start": 111, "lazy": 0, "on demand": 0, "off": 0}
    assert got["warnings"] == []


def test_dangling_root_claude_link_in_a_git_tree(tmp_path):
    """Thread 11: remote sources are GitTrees; a dangling CLAUDE.md link does not turn AGENTS.md off there."""
    root = tmp_path / "r"
    tree = _linked_repo(root, {"AGENTS.md": "a" * 10}, {"CLAUDE.md": "missing.md"})
    got = budget(tree, NAMES)
    assert _by_path(got)[("AGENTS.md", "guidance")]["when"] == "start"
    assert got["warnings"] == ["CLAUDE.md: not readable (not found, broken link, or outside tree), counted as 0"]
    assert got["totals"] == {"start": 10, "lazy": 0, "on demand": 0, "off": 0}


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
        "email.md": "nope",  # Exists but a@email.md has lookbehind, not an import
        "etc/passwd.md": "secret",  # Real file but @../ escapes tree
        "fenced.md": "code",  # Real file but `@` is in code span
    })
    got = budget(tree, NAMES)
    entries_paths = [(e["path"], e["kind"]) for e in got["entries"]]
    # example.md is followed; email.md, etc/passwd.md, and fenced.md are escaped/skipped
    assert ("example.md", "import") in entries_paths
    assert not any("email" in p or "etc/passwd" in p or "fenced" in p for p, k in entries_paths)


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
    _tree(tmp_path, {
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


def test_dangling_and_out_of_tree_imports_agree_across_work_and_git_trees(tmp_path):
    """A link that dangles or leaves the tree is no import, on a working tree or a commit."""
    (tmp_path / "outside.md").write_text("o" * 30, encoding="utf-8")
    files = {"CLAUDE.md": "see @notes.md and @gone.md"}
    links = {"notes.md": "../outside.md", "gone.md": "missing.md"}
    work = tmp_path / "w"
    work.mkdir()
    for rel, text in files.items():
        (work / rel).write_text(text, encoding="utf-8")
    for rel, text in links.items():
        _link(work / rel, text)
    git_root = tmp_path / "g"
    git_root.mkdir()
    for got in (budget(WorkTree(work), NAMES), budget(_linked_repo(git_root, files, links), NAMES)):
        assert [e["path"] for e in got["entries"]] == ["CLAUDE.md"]
        assert got["warnings"] == []


def test_a_claude_alias_group_with_a_root_alias_loads_at_start(tmp_path):
    """.claude/CLAUDE.md -> ../.agents/CLAUDE.md: the shown path is nested, but a root alias reads it at start."""
    tree = _tree(tmp_path, {".agents/CLAUDE.md": "a" * 50, "CLAUDE.md": "r"})
    (tmp_path / ".claude").mkdir()
    _link(tmp_path / ".claude" / "CLAUDE.md", "../.agents/CLAUDE.md")
    got = budget(tree, NAMES)
    assert _by_path(got)[(".agents/CLAUDE.md", "guidance")]["when"] == "start"
    assert got["totals"] == {"start": 51, "lazy": 0, "on demand": 0, "off": 0}


def test_unreadable_skill_warnings_name_the_cause(tmp_path):
    tree = _tree(tmp_path, {})
    d = tmp_path / ".claude" / "skills"
    (d / "bin").mkdir(parents=True)
    (d / "bin" / "SKILL.md").write_bytes(b"\xff\xff")
    (d / "dang").mkdir()
    _link(d / "dang" / "SKILL.md", "nowhere")
    got = budget(tree, NAMES)
    tail = "body counted as 0; the listing falls back to the directory name"
    assert sorted(got["warnings"]) == [
        f".claude/skills/bin/SKILL.md: not readable (non-UTF-8 or binary), {tail}",
        f".claude/skills/dang/SKILL.md: not readable (not found, broken link, or outside tree), {tail}",
    ]



def test_a_plain_missing_import_is_not_counted(tmp_path):
    """`@missing.md` that names no file adds no row; only the real import does."""
    tree = _tree(tmp_path, {"CLAUDE.md": "see @missing.md and @real.md", "real.md": "r" * 7})
    got = budget(tree, NAMES)
    assert sorted(e["path"] for e in got["entries"]) == ["CLAUDE.md", "real.md"]
    assert got["totals"]["start"] == len("see @missing.md and @real.md") + 7
    assert got["warnings"] == []


def test_a_code_span_wrapped_onto_the_next_line_hides_its_import(tmp_path):
    """A span may wrap one line (CommonMark); a blank line ends it, so the import after counts."""
    tree = _tree(tmp_path, {"CLAUDE.md": "see `code\n@a.md` and @b.md\n\n`open\n\n@c.md",
                            "a.md": "a", "b.md": "b", "c.md": "c"})
    assert sorted(e["path"] for e in budget(tree, NAMES)["entries"]) == ["CLAUDE.md", "b.md", "c.md"]


def test_code_span_stripping_is_linear_on_backtick_runs():
    """Long backtick runs once made the code-span regex backtrack exponentially (CodeQL py/redos)."""
    import time

    from dokimi.repos.budget import _strip_code_spans

    for text in ("`" * 50_000, "`" + "`a" * 50_000, "``" + "` " * 50_000 + "\n\n@x.md"):
        started = time.monotonic()
        _strip_code_spans(text)
        assert time.monotonic() - started < 1
    assert _strip_code_spans("a `b` c ``d`e`` f") == "a  c  f"
    assert _strip_code_spans("`a\nb` @x.md") == " @x.md"
    assert _strip_code_spans("`a\n\nb` @x.md") == "`a\n\nb` @x.md"


def test_an_import_path_runs_to_the_next_whitespace(tmp_path):
    """Claude Code 2.1.300 loaded none of `@x.md.`, `@x.md,`, `(@x.md)`, `"@x.md"`; it loaded `- @x.md`."""
    texts = {"dot": "See @dot.md.", "comma": "@comma.md, then", "paren": "(@paren.md)",
             "quoted": '"@quoted.md"', "item": "- @item.md", "tail": "last @tail.md"}
    tree = _tree(tmp_path, {"CLAUDE.md": "\n\n".join(texts.values()), **{f"{n}.md": "x" for n in texts}})
    got = budget(tree, NAMES)
    assert sorted(e["path"] for e in got["entries"] if e["kind"] == "import") == ["item.md", "tail.md"]


def test_a_code_span_does_not_wrap_onto_a_new_block(tmp_path):
    """Claude Code 2.1.300 loaded each import below: a heading, quote, list item or underline ends the span, and so does the end of a heading."""
    tick = "`"
    texts = {"heading": "## {i} ", "quote": "> {i} ", "dash": "- {i} ", "star": "* {i} ", "plus": "+ {i} ",
             "ordered": "1. {i} ", "underline": "---\n{i} "}
    files = {f"{n}.md": "x" for n in [*texts, "para", "hash"]}
    blocks = [f"x {tick}y\n" + t.format(i=f"@{n}.md") + f"{tick}z" for n, t in texts.items()]
    blocks += [f"x {tick}y\n@para.md {tick}z", f"#hash {tick}y\n@hash.md {tick}z"]  # one paragraph: hidden
    blocks.append(f"- Do not put a {tick} in names\n- See @list.md\n- Run {tick}make{tick}")
    blocks.append(f"# Head {tick}y\n@after.md {tick}z")
    tree = _tree(tmp_path, {"CLAUDE.md": "\n\n".join(blocks), **files, "list.md": "x", "after.md": "x"})
    got = budget(tree, NAMES)
    imported = {e["path"] for e in got["entries"] if e["kind"] == "import"}
    assert imported == {f"{n}.md" for n in texts} | {"list.md", "after.md"}


def test_fence_edges(tmp_path):
    """An unclosed fence hides the rest; ```x``` at a line start is a span, not a fence; a longer closer closes."""
    f = "`" * 3
    cases = {
        "unclosed": (f"@a.md\n{f}\n@b.md\n", {"a.md"}),
        "span": (f"{f}make test{f} runs the suite.\n\nSee @a.md\n", {"a.md"}),
        "longer": (f"{f}\n@b.md\n{f}`\n@a.md\n", {"a.md"}),
        "tilde_unclosed": ("~~~\n@b.md\n", set()),
        "setext": ("x `y\nabc\n===\n@a.md `z\n", {"a.md"}),  # an `===` underline ends the span
    }
    for name, (text, want) in cases.items():
        tree = _tree(tmp_path / name, {"CLAUDE.md": text, "a.md": "a", "b.md": "b"})
        got = {e["path"] for e in budget(tree, NAMES)["entries"] if e["kind"] == "import"}
        assert got == want, name


def test_import_paths_with_dashes_and_dot_segments(tmp_path):
    tree = _tree(tmp_path, {"CLAUDE.md": "@docs/my-style.md and @./local.md", "docs/my-style.md": "s" * 3,
                            "local.md": "l" * 2})
    got = _by_path(budget(tree, NAMES))
    assert got[("docs/my-style.md", "import")]["chars"] == 3
    assert got[("local.md", "import")]["chars"] == 2


def test_disable_model_invocation_false_keeps_the_listing(tmp_path):
    for i, value in enumerate(("false", "no", "0")):
        text = f"---\nname: s\ndescription: Run it.\ndisable-model-invocation: {value}\n---\nb"
        got = _by_path(budget(_tree(tmp_path / str(i), {".claude/skills/s/SKILL.md": text}), NAMES))
        assert got[(".claude/skills/s/SKILL.md", "skill listing")]["chars"] == len("s") + len("Run it.")


def test_two_word_partial_overlap_is_not_a_duplicate(tmp_path):
    tree = _tree(tmp_path, {".claude/skills/one/SKILL.md": _skill("one", "Deploy app"),
                            ".claude/skills/two/SKILL.md": _skill("two", "Deploy app now")})
    assert budget(tree, NAMES)["duplicate_skills"] == []


def test_cli_rows_inside_a_group_are_larger_first(tmp_path, capsys):
    _tree(tmp_path, {"CLAUDE.md": "@small.md @big.md", "small.md": "s" * 3, "big.md": "b" * 300})
    out = _run(["repo", "budget", str(tmp_path)], capsys).out
    assert out.index("| big.md |") < out.index("| small.md |")


def test_an_import_through_a_linked_directory_agrees_across_trees(tmp_path):
    files = {"CLAUDE.md": "see @docs/a.md", "real/a.md": "aaaa"}
    links = {"docs": "real"}
    work = tmp_path / "w"
    for rel, text in files.items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(text, encoding="utf-8")
    _link(work / "docs", "real")
    git_root = tmp_path / "g"
    git_root.mkdir()
    for got in (budget(WorkTree(work), NAMES), budget(_linked_repo(git_root, files, links), NAMES)):
        assert _by_path(got)[("docs/a.md", "import")]["chars"] == 4
        assert got["warnings"] == []


def test_link_loops_warn_instead_of_crashing(tmp_path, monkeypatch):
    """Before Python 3.13 reading through a link loop raises RuntimeError; simulate it on any version."""
    import errno

    original = WorkTree.read_text

    def read_text(self, path):
        try:
            return original(self, path)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise RuntimeError(f"Symlink loop from {path}") from exc
            raise

    monkeypatch.setattr(WorkTree, "read_text", read_text)
    tree = _tree(tmp_path, {"AGENTS.md": "see @loop.md"})
    _link(tmp_path / "CLAUDE.md", "CLAUDE.md")
    _link(tmp_path / "loop.md", "loop.md")
    (tmp_path / ".claude" / "skills" / "s").mkdir(parents=True)
    _link(tmp_path / ".claude" / "skills" / "s" / "SKILL.md", "SKILL.md")
    got = budget(tree, NAMES)  # no RuntimeError
    assert any(w.startswith("CLAUDE.md: not readable") for w in got["warnings"])
    assert any(w.startswith(".claude/skills/s/SKILL.md: not readable") for w in got["warnings"])
    assert {e["path"] for e in got["entries"]} >= {"CLAUDE.md", ".claude/skills/s/SKILL.md"}


def _chain(tmp_path, links):
    files = {"CLAUDE.md": "@c1.md\n"}
    for i in range(1, links + 1):
        files[f"c{i}.md"] = (f"@c{i + 1}.md\n" if i < links else "end\n")
    return _tree(tmp_path, files)


def test_imports_stop_at_the_depth_limit_and_warn(tmp_path):
    got = budget(_chain(tmp_path, 5), NAMES, import_max_depth=4)  # c5 is the fifth hop
    entries = _by_path(got)
    assert all((f"c{i}.md", "import") in entries for i in range(1, 5))
    assert ("c5.md", "import") not in entries
    assert any("c4.md" in w and "c5.md" in w and "4-hop" in w for w in got["warnings"])


def test_imports_exactly_at_the_depth_limit_do_not_warn(tmp_path):
    got = budget(_chain(tmp_path, 4), NAMES, import_max_depth=4)
    assert ("c4.md", "import") in _by_path(got)
    assert not any("hop" in w for w in got["warnings"])


def test_imports_are_unlimited_without_a_depth_limit(tmp_path):
    got = budget(_chain(tmp_path, 8), NAMES)
    assert ("c8.md", "import") in _by_path(got) and not got["warnings"]


def test_the_claude_code_profile_limits_imports_to_four_hops():
    from dokimi.repos.harness_profile import load_profile
    assert load_profile("claude-code").import_max_depth == 4
