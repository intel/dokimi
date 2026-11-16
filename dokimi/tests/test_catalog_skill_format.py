# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Skill formats: OpenCode's SKILL.md rules, and one note for a host dokimi holds no spec for.

Before this, every ``.opencode`` SKILL.md that did not follow the Agent Skills spec was an error:
78% of the sample. OpenCode makes all frontmatter optional and reads the ID from the path
(https://opencode.ai/docs/skills/); the spec-only checks are not its rules.
"""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from dokimi.external.skill_format import (
    FORMAT_AGENT_SKILL,
    FORMAT_OPENCODE,
    FORMAT_UNSUPPORTED,
    skill_format,
)


def main(argv):
    """Run the CLI on ``argv``; the exit code (0 when nothing raised)."""
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except SystemExit as exc:
        return exc.code
    return 0


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rows(root, capsys, *extra):
    main(["catalog", "check", "--root", str(root), "--kind", "skills", "--format", "json", *extra])
    return {row["relpath"]: row for row in json.loads(capsys.readouterr().out)["skills"]}


def _codes(row):
    return {(f["level"], f["code"]) for f in row["findings"]}


_LONG = "Does a thing with many words. Use when asked. " * 25  # over 1024 characters


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        (".opencode/skills/a/SKILL.md", FORMAT_OPENCODE),
        (".opencode/skill/a/SKILL.md", FORMAT_OPENCODE),  # the corpus's singular form, not in the docs
        ("scripts/fixtures/x/.opencode/skills/py/SKILL.md", FORMAT_OPENCODE),
        (".claude/skills/a/SKILL.md", FORMAT_AGENT_SKILL),
        (".agents/skills/a/SKILL.md", FORMAT_AGENT_SKILL),
        ("skills/a/SKILL.md", FORMAT_AGENT_SKILL),  # no host directory: the spec
        ("SKILL.md", FORMAT_AGENT_SKILL),
        (".kiro/skills/a/SKILL.md", FORMAT_UNSUPPORTED),
    ],
)
def test_the_host_is_read_from_the_path_below_the_root(tmp_path, rel, expected):
    path = _write(tmp_path, rel, "---\nname: a\ndescription: x\n---\n")
    assert skill_format(path, tmp_path) == expected


def test_a_hidden_directory_above_the_root_is_not_a_host(tmp_path):
    root = tmp_path / ".cache" / "checkout"
    path = _write(root, "skills/a/SKILL.md", "---\nname: a\ndescription: x\n---\n")
    assert skill_format(path, root) == FORMAT_AGENT_SKILL


def test_a_skill_reviewed_alone_reads_its_own_path(tmp_path):
    skill_dir = tmp_path / ".opencode" / "skills" / "a"
    path = _write(skill_dir.parent.parent, ".opencode/skills/a/SKILL.md", "# plain\n")
    assert skill_format(path, skill_dir) == FORMAT_OPENCODE


def test_opencode_plain_markdown_is_a_warning_and_fails_no_scan(tmp_path, capsys):
    _write(tmp_path, ".opencode/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    rows = _rows(tmp_path, capsys)
    row = rows[".opencode/skills/quantum"]
    assert row["state"] == "warn"
    assert row["error"] is None
    assert ("warn", "opencode-no-description") in _codes(row)
    assert not any(code == "not-a-skill-no-frontmatter" for _, code in _codes(row))
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0


def test_the_same_plain_markdown_under_a_spec_host_is_still_an_error(tmp_path, capsys):
    _write(tmp_path, ".claude/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    row = _rows(tmp_path, capsys)[".claude/skills/quantum"]
    assert row["state"] == "error"
    assert ("error", "not-a-skill-no-frontmatter") in _codes(row)
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills"]) == 1


def test_opencode_frontmatter_without_a_description_is_a_warning(tmp_path, capsys):
    _write(tmp_path, ".opencode/skills/quiet/SKILL.md", "---\nname: quiet\n---\n\n# Quiet\n")
    row = _rows(tmp_path, capsys)[".opencode/skills/quiet"]
    assert row["state"] == "warn"
    assert row["error"] is None
    assert ("warn", "opencode-no-description") in _codes(row)


def test_opencode_name_rules_are_advisory(tmp_path, capsys):
    # OpenCode: name is a display label, not checked against the directory; the kebab-case rule is advice.
    text = f"---\nname: Other_Name\ndescription: {_LONG}\n---\n\n# Body\n"
    _write(tmp_path, ".opencode/skills/my-skill/SKILL.md", text)
    row = _rows(tmp_path, capsys)[".opencode/skills/my-skill"]
    codes = _codes(row)
    assert ("warn", "name-format") in codes
    assert not any(code in ("name-mismatch", "description-too-long") for _, code in codes)
    assert row["state"] == "warn"
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0


def test_the_same_name_rules_under_a_spec_host_are_errors(tmp_path, capsys):
    text = f"---\nname: Other_Name\ndescription: {_LONG}\n---\n\n# Body\n"
    _write(tmp_path, ".claude/skills/my-skill/SKILL.md", text)
    row = _rows(tmp_path, capsys)[".claude/skills/my-skill"]
    codes = _codes(row)
    assert {("error", "name-mismatch"), ("error", "name-format"), ("error", "description-too-long")} <= codes
    assert row["state"] == "error"


def test_an_unsupported_host_gets_one_note_and_no_spec_errors(tmp_path, capsys):
    text = f"---\nname: other\ndescription: {_LONG}\n---\n\n# Body\n"
    _write(tmp_path, ".kiro/skills/my-skill/SKILL.md", text)
    row = _rows(tmp_path, capsys)[".kiro/skills/my-skill"]
    assert [code for _, code in _codes(row)] == ["unsupported-skill-format"]
    assert row["state"] == "ok"
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0
    assert "not checked" in capsys.readouterr().out


def test_an_unsupported_host_without_a_description_is_a_note_not_an_error(tmp_path, capsys):
    _write(tmp_path, ".kiro/skills/quiet/SKILL.md", "---\nname: quiet\n---\n\n# Quiet\n")
    row = _rows(tmp_path, capsys)[".kiro/skills/quiet"]
    assert row["state"] == "ok"
    assert row["error"] is None
    assert _codes(row) == {("note", "unsupported-skill-format")}


def test_an_unsupported_host_plain_markdown_is_a_note_not_a_failed_load(tmp_path, capsys):
    _write(tmp_path, ".kiro/skills/quantum/SKILL.md", "# Quantum\n\nA plain skill.\n")
    row = _rows(tmp_path, capsys)[".kiro/skills/quantum"]
    assert row["state"] == "ok"
    assert row["error"] is None
    assert _codes(row) == {("note", "unsupported-skill-format")}
    assert main(["catalog", "check", "--root", str(tmp_path), "--kind", "skills", "--strict"]) == 0
