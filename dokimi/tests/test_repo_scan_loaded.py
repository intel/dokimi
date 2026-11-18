# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo scan` without a manifest also checks the rules and @ imports Claude Code loads with CLAUDE.md."""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.cli import build_parser


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _scan(capsys, *argv: str) -> dict:
    args = build_parser().parse_args(["repo", "scan", *argv, "--json"])
    args.func(args)
    return json.loads(capsys.readouterr().out)["data"]


def _fails(payload: dict) -> set[tuple[str, str]]:
    return {(c["file"], c["claim"]) for c in payload["claims"] if c["status"] == "fail"}


LOADED = {
    "src/real.py": "x = 1\n",
    "CLAUDE.md": "Run `src/real.py`.\n@docs/agent.md\n@package.json\n",
    "docs/agent.md": "The entry point is `src/gone_import.py`.\n",
    "package.json": '{"main": "src/gone_json.py"}\n',
    ".claude/rules/build.md": "---\npaths: []\n---\nBuild from `src/gone_rule.py`.\n",
    "lib/.claude/rules/lib.md": "The library lives in `src/gone_lazy.py`.\n",
}


def test_a_default_scan_checks_the_rules_and_imports_claude_code_loads(tmp_path, capsys):
    payload = _scan(capsys, str(_write(tmp_path / "r", LOADED)))
    assert payload["files"] == [".claude/rules/build.md", "CLAUDE.md", "docs/agent.md", "lib/.claude/rules/lib.md"]
    assert _fails(payload) == {(".claude/rules/build.md", "src/gone_rule.py"), ("docs/agent.md", "src/gone_import.py"),
                               ("lib/.claude/rules/lib.md", "src/gone_lazy.py")}


def test_a_written_manifest_scans_only_what_it_names(tmp_path, capsys):
    root = _write(tmp_path / "r", LOADED)
    manifest = tmp_path / "m.yaml"
    manifest.write_text("schema: repo_manifest.v1\nid: r\nguidance:\n  globs: [CLAUDE.md]\n", encoding="utf-8")
    payload = _scan(capsys, str(root), "--manifest-file", str(manifest))
    assert payload["files"] == ["CLAUDE.md"]
    assert _fails(payload) == set()


def test_a_repository_without_rules_gets_no_new_warning(tmp_path, capsys):
    payload = _scan(capsys, str(_write(tmp_path / "r", {"CLAUDE.md": "# guidance\n"})))
    assert payload["files"] == ["CLAUDE.md"]
    assert not [w for w in payload["warnings"] if "rules" in w or "import" in w]


def test_an_import_through_a_linked_directory_is_checked_as_the_file_it_reaches(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "@lnk/x.md\n",
                                   "docs/x.md": "See `src/gone_dl.py`.\n"})
    (root / "lnk").symlink_to("docs")
    payload = _scan(capsys, str(root))
    assert payload["files"] == ["CLAUDE.md", "docs/x.md"]
    assert _fails(payload) == {("docs/x.md", "src/gone_dl.py")}


def test_a_markdown_import_that_links_to_another_kind_of_file_is_not_read_as_guidance(tmp_path, capsys):
    root = _write(tmp_path / "r", {"CLAUDE.md": "@cfg.md\n", "package.json": '{"main": "`src/gone.py`"}\n'})
    (root / "cfg.md").symlink_to("package.json")
    assert _scan(capsys, str(root))["files"] == ["CLAUDE.md"]


def test_an_import_budget_counts_as_guidance_is_still_checked(tmp_path, capsys):
    # CLAUDE.local.md is a guidance name, so budget lists it as guidance rather than as an import; the
    # default globs do not match it, so it must be checked as the loaded file it is, not dropped
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "@CLAUDE.local.md\n",
                                   "CLAUDE.local.md": "The entry point is `src/gone_local.py`.\n"})
    payload = _scan(capsys, str(root))
    assert "CLAUDE.local.md" in payload["files"]
    assert _fails(payload) == {("CLAUDE.local.md", "src/gone_local.py")}


def test_an_unimported_claude_local_md_is_checked_and_the_note_does_not_call_it_a_rule_or_import(tmp_path, capsys):
    # Claude Code loads CLAUDE.local.md with no import, so the default scan checks it; the stderr note must
    # say what it checks, not call the file an @ import or a rule
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# g\n",
                                   "CLAUDE.local.md": "The entry point is `src/gone_unimported.py`.\n"})
    args = build_parser().parse_args(["repo", "scan", str(root), "--json"])
    args.func(args)
    captured = capsys.readouterr()
    assert _fails(json.loads(captured.out)["data"]) == {("CLAUDE.local.md", "src/gone_unimported.py")}
    assert "@ imported" not in captured.err
    assert "CLAUDE.local.md" in captured.err


def test_an_explicit_import_of_a_guidance_name_is_still_checked(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "@private/CLAUDE.local.md\n",
                                   "private/CLAUDE.local.md": "See `src/gone_priv.py`.\n"})
    payload = _scan(capsys, str(root))
    assert _fails(payload) == {("private/CLAUDE.local.md", "src/gone_priv.py")}


def test_a_rule_linked_to_a_guidance_name_is_still_checked(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   "private/CLAUDE.local.md": "Stale `src/gone_rule_link.py`.\n"})
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/x.md").symlink_to("../../private/CLAUDE.local.md")
    payload = _scan(capsys, str(root))
    assert _fails(payload) == {("private/CLAUDE.local.md", "src/gone_rule_link.py")}


def test_a_rules_link_to_a_directory_makes_the_default_scan_incomplete(tmp_path, capsys):
    # budget warns that the rules behind a linked .claude/rules are not counted; the scan must say so too,
    # or --fail-on-fail reads the unchecked rules as a clean pass
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   "shared/rules/a.md": "See `src/gone_a.py`.\n"})
    (root / ".claude").mkdir()
    (root / ".claude/rules").symlink_to("../shared/rules")
    payload = _scan(capsys, str(root))
    assert payload["incomplete"]
    assert any(".claude/rules" in r for r in payload["incomplete_reasons"])


def test_a_rule_is_scanned_without_its_frontmatter(tmp_path, capsys):
    # Claude Code removes a rule's frontmatter before it loads the rule: a path in it is not read as text, and a
    # path in the body is, on its own line
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   ".claude/rules/b.md": "---\ndescription: run before `src/gone_b.py`\n---\n"
                                                         "See `src/gone_body.py`.\n"})
    payload = _scan(capsys, str(root))
    assert ".claude/rules/b.md" in payload["files"]
    assert _fails(payload) == {(".claude/rules/b.md", "src/gone_body.py")}
    assert {c["line"] for c in payload["claims"] if c["file"] == ".claude/rules/b.md" and c["status"] == "fail"} == {4}


def test_a_code_fence_in_rule_frontmatter_does_not_hide_the_body(tmp_path, capsys):
    # a fence opened in the frontmatter used to run on to the body, which then read as fence text: its path unchecked
    rule = "---\ndescription: |\n  ```text\n---\nSee `src/gone_fence.py`.\n"
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n", ".claude/rules/f.md": rule})
    assert _fails(_scan(capsys, str(root))) == {(".claude/rules/f.md", "src/gone_fence.py")}


def test_a_line_break_character_in_rule_frontmatter_counts_as_a_line(tmp_path, capsys):
    # U+2028 ends a line for the scan too: the frontmatter's path is not read, the body's is checked
    rule = "---\ndescription: a\u2028b\u2028see `src/gone_fm.py`\n---\nSee `src/gone_body_u.py`.\n"
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n", ".claude/rules/u.md": rule})
    assert _fails(_scan(capsys, str(root))) == {(".claude/rules/u.md", "src/gone_body_u.py")}


def test_a_rule_linked_to_a_non_markdown_file_is_checked(tmp_path, capsys):
    # the contract says every rule file is checked; the link's target is no reason to skip it
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   "shared.txt": "See `src/gone_txt.py`.\n"})
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/x.md").symlink_to("../../shared.txt")
    assert _fails(_scan(capsys, str(root))) == {(".claude/rules/x.md", "src/gone_txt.py")}


def test_a_rule_stays_checked_when_claude_local_md_links_to_the_same_file(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   "shared.txt": "See `src/gone_shared.py`.\n"})
    (root / "CLAUDE.local.md").symlink_to("shared.txt")
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/x.md").symlink_to("../../shared.txt")
    payload = _scan(capsys, str(root))
    assert ".claude/rules/x.md" in payload["files"]
    assert _fails(payload) == {(".claude/rules/x.md", "src/gone_shared.py"), ("CLAUDE.local.md", "src/gone_shared.py")}


def test_a_default_scan_does_not_check_skills(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   ".claude/skills/s/SKILL.md": "Run `src/gone_skill.py`.\n"})
    payload = _scan(capsys, str(root))
    assert payload["files"] == ["CLAUDE.md"]
    assert _fails(payload) == set()


def test_guidance_frontmatter_is_still_scanned(tmp_path, capsys):
    # only a rule loses its frontmatter: a CLAUDE.md is loaded with it
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "---\nnote: see `src/gone_fm.py`\n---\n# g\n"})
    assert _fails(_scan(capsys, str(root))) == {("CLAUDE.md", "src/gone_fm.py")}


def test_the_note_counts_only_the_files_the_default_globs_do_not_match(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "@.github/instructions/a.md\n",
                                   ".github/instructions/a.md": "x\n", ".claude/rules/b.md": "y\n"})
    args = build_parser().parse_args(["repo", "scan", str(root), "--json"])
    args.func(args)
    assert "also checking 1 files" in capsys.readouterr().err


def test_a_named_file_that_matches_no_glob_adds_no_coverage_warning(tmp_path, capsys):
    # the rule and the import are named through `also`; a coverage warning for either would be a false alarm
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "@docs/agent.md\n",
                                   "docs/agent.md": "Run `src/real.py`.\n",
                                   ".claude/rules/build.md": "Build with `src/real.py`.\n"})
    payload = _scan(capsys, str(root))
    assert not [w for w in payload["warnings"] if "docs/agent.md" in w or "build.md" in w]


def test_a_rule_folded_into_a_guidance_alias_of_a_text_file_is_checked(tmp_path, capsys):
    # CLAUDE.local.md and a rule both link to one .txt file: budget counts it once, as the guidance name, and the
    # guidance entry was dropped for not being Markdown, so neither alias was scanned and no warning was printed
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "Run `src/real.py`.\n",
                                   "shared.txt": "See `src/gone_alias.py`.\n"})
    (root / "CLAUDE.local.md").symlink_to("shared.txt")
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/x.md").symlink_to("../../shared.txt")
    payload = _scan(capsys, str(root))
    assert _fails(payload) == {(".claude/rules/x.md", "src/gone_alias.py"), ("CLAUDE.local.md", "src/gone_alias.py")}


def test_a_rule_linked_to_a_guidance_file_is_scanned_without_its_frontmatter(tmp_path, capsys):
    # the rule route removes the frontmatter of the file it links to, even when that file is listed as guidance:
    # the fence in its frontmatter must not hide the stale path in its body
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   "private/CLAUDE.local.md": "---\ndescription: |\n  ```text\n---\n"
                                                              "See `src/gone_fm_link.py`.\n"})
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/x.md").symlink_to("../../private/CLAUDE.local.md")
    payload = _scan(capsys, str(root))
    assert _fails(payload) == {("private/CLAUDE.local.md", "src/gone_fm_link.py")}


def test_a_rule_linked_to_agents_md_keeps_the_frontmatter_claims_of_agents_md(tmp_path, capsys):
    # AGENTS.md is read with its frontmatter by Codex and Copilot, so a rule linking to it must not blank it
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "AGENTS.md": "---\nnote: see `src/gone_f.py`\n---\nhi\n"})
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/agents.md").symlink_to("../../AGENTS.md")
    manifest = root / "m.yaml"
    manifest.write_text("schema: repo_manifest.v1\nid: t\nguidance:\n  globs: [AGENTS.md]\n", encoding="utf-8")
    assert _fails(_scan(capsys, str(root), "--manifest-file", str(manifest))) == {("AGENTS.md", "src/gone_f.py")}


def test_a_rule_linked_to_claude_md_keeps_the_frontmatter_claims_of_claude_md(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n",
                                   "CLAUDE.md": "---\ndescription: see `src/gone_top.py`\n---\nhi\n"})
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/x.md").symlink_to("../../CLAUDE.md")
    assert _fails(_scan(capsys, str(root))) == {("CLAUDE.md", "src/gone_top.py")}


def test_a_claude_local_md_linked_to_a_text_file_is_checked(tmp_path, capsys):
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n", "CLAUDE.md": "# guidance\n",
                                   "shared.txt": "See `src/gone_txt.py`.\n"})
    (root / "CLAUDE.local.md").symlink_to("shared.txt")
    assert _fails(_scan(capsys, str(root))) == {("CLAUDE.local.md", "src/gone_txt.py")}


def test_a_deeply_nested_mcp_json_does_not_stop_the_scan(tmp_path, capsys):
    root = _write(tmp_path / "r", {"CLAUDE.md": "# guidance\n", ".mcp.json": "[" * 10000 + "0" + "]" * 10000})
    assert _scan(capsys, str(root))["files"] == ["CLAUDE.md"]


def test_a_rule_linked_to_claude_md_is_scanned_without_its_frontmatter(tmp_path, capsys):
    # the frontmatter fence of CLAUDE.md is read as it is there, but the rule route loads the body without it
    root = _write(tmp_path / "r", {"src/real.py": "x = 1\n",
                                   "private/CLAUDE.md": "---\ndescription: |\n  ```text\n---\n"
                                                        "See `src/gone_fm_link.py`.\n"})
    (root / ".claude/rules").mkdir(parents=True)
    (root / ".claude/rules/x.md").symlink_to("../../private/CLAUDE.md")
    assert (".claude/rules/x.md", "src/gone_fm_link.py") in _fails(_scan(capsys, str(root)))
