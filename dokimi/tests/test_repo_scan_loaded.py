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
    return json.loads(capsys.readouterr().out)


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
