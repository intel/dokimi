# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1089: hidden characters in the bundled files of a skill whose front matter is broken YAML."""

from __future__ import annotations

from pathlib import Path

from dokimi.external import read_skill_manifest, review_skills

BROKEN = "---\nname: [broken\ndescription: x: y: [\n---\nbody\n"
VALID = "---\nname: parent\ndescription: Use when testing.\n---\nbody\n"


def _hidden(root: Path) -> list[str]:
    reviews = review_skills(read_skill_manifest(root))
    return [f.message for r in reviews if r for f in r.findings if f.code == "hidden-unicode"]


def _broken(root: Path, notes: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(BROKEN, encoding="utf-8")
    (root / "notes.md").write_text(notes, encoding="utf-8")


def test_broken_yaml_skill_reports_tag_character_in_bundled_file(tmp_path):
    _broken(tmp_path / "sub-tag", "hi " + chr(0xE0041) + " x\n")
    [message] = _hidden(tmp_path)
    assert message.startswith("notes.md line 1: U+E0041")


def test_nested_broken_yaml_skill_reports_bidi_character_under_valid_parent(tmp_path):
    (tmp_path / "parent").mkdir()
    (tmp_path / "parent" / "SKILL.md").write_text(VALID, encoding="utf-8")
    _broken(tmp_path / "parent" / "sub-bidi", "hi " + chr(0x202E) + " x\n")
    [message] = _hidden(tmp_path)
    assert message.startswith("notes.md line 1: U+202E")


def test_broken_yaml_skill_with_ascii_bundled_file_has_no_hidden_unicode(tmp_path):
    """#1089: a broken-YAML skill whose bundled file is plain ASCII gets no `hidden-unicode` finding."""
    _broken(tmp_path / "sub-ascii", "hi there\n")
    assert _hidden(tmp_path) == []



def test_tag_only_front_matter_skill_reports_bidi_character_in_bundled_file(tmp_path):
    """#1089: a block `opens_frontmatter` rejects (no YAML key) is a not-a-skill row, and its bundled file is still read."""
    (tmp_path / "parent").mkdir()
    (tmp_path / "parent" / "SKILL.md").write_text(VALID, encoding="utf-8")
    sub = tmp_path / "parent" / "sub"
    sub.mkdir()
    (sub / "SKILL.md").write_text("---\n!!unknown value\n---\nbody\n", encoding="utf-8")
    (sub / "notes.md").write_text("hi " + chr(0x202E) + " x\n", encoding="utf-8")
    [message] = _hidden(tmp_path)
    assert message.startswith("notes.md line 1: U+202E")


def test_unsupported_host_note_says_content_was_checked(tmp_path):
    """#1089: an unloaded skill of an unsupported host now gets the content check, and its note says so."""
    skill = tmp_path / ".cursor" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
    (skill / "notes.md").write_text("hi " + chr(0x202E) + " x\n", encoding="utf-8")
    findings = [f for r in review_skills(read_skill_manifest(tmp_path)) for f in r.findings]
    assert any(f.code == "hidden-unicode" for f in findings)
    [note] = [f.message for f in findings if f.code == "unsupported-skill-format"]
    assert "only its commands and its Bash grant" not in note


def test_indented_closing_delimiter_skill_reports_bidi_character_under_unsupported_host(tmp_path):
    """#1089: a broken block whose closing `---` is indented keeps its load error, and its bundled file is still read."""
    parent = tmp_path / ".cursor" / "skills" / "parent"
    (parent / "bad").mkdir(parents=True)
    (parent / "SKILL.md").write_text(VALID, encoding="utf-8")
    (parent / "bad" / "SKILL.md").write_text("---\nname: [broken\ndescription: x\n  ---\nbody\n", encoding="utf-8")
    (parent / "bad" / "notes.md").write_text("hi " + chr(0x202E) + " x\n", encoding="utf-8")
    [message] = _hidden(tmp_path)
    assert message.startswith("notes.md line 1: U+202E")
