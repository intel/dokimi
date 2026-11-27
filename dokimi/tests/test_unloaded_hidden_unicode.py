# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1089: hidden characters in the bundled files of a skill whose front matter is broken YAML."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.external import read_skill_manifest, review_skill_dir, review_skills

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
    findings = [f for r in review_skills(read_skill_manifest(tmp_path)) if r for f in r.findings]
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


def test_standalone_review_of_indented_closing_delimiter_skill_keeps_load_error(tmp_path):
    """#1089: `review_skill_dir` on a skill that fails to load stays `error` when a bundled file has a hidden character."""
    bad = tmp_path / ".cursor" / "skills" / "parent" / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: [broken\ndescription: x\n  ---\nbody\n", encoding="utf-8")
    (bad / "notes.md").write_text("hi " + chr(0x202E) + " x\n", encoding="utf-8")
    review = review_skill_dir(bad)
    codes = [f.code for f in review.findings]
    assert review.state == "error"
    assert "not-loaded" in codes and "hidden-unicode" in codes


def test_missing_description_skill_reports_hidden_unicode_in_bundled_file(tmp_path):
    """#1089: a closed block with no description fails to load, and its bundled file is still read."""
    (tmp_path / "skills" / "bad").mkdir(parents=True)
    (tmp_path / "skills" / "bad" / "SKILL.md").write_text("---\nname: bad\n---\nbody\n", encoding="utf-8")
    (tmp_path / "skills" / "bad" / "notes.md").write_text("hi " + chr(0x202E) + " x\n", encoding="utf-8")
    [message] = _hidden(tmp_path)
    assert message.startswith("notes.md line 1: U+202E")


def test_cli_row_of_unloaded_skill_has_one_load_error_and_no_not_loaded(tmp_path, capsys):
    """docs/finding-codes.md: the CLI never emits `not-loaded`; the row's `load-error` is its one load error."""
    (tmp_path / "skills" / "bad").mkdir(parents=True)
    (tmp_path / "skills" / "bad" / "SKILL.md").write_text("---\nname: bad\n---\nbody\n", encoding="utf-8")
    (tmp_path / "skills" / "bad" / "notes.md").write_text("hi " + chr(0x202E) + " x\n", encoding="utf-8")
    args = build_parser().parse_args(["catalog", "check", "--root", str(tmp_path), "--json"])
    with pytest.raises(SystemExit):
        args.func(args)
    doc = json.loads(capsys.readouterr().out)
    [subject] = [s for s in doc["subjects"] if s["subject"] == "bad"]
    assert [f["code"] for f in subject["findings"]] == ["load-error", "hidden-unicode"]
    assert "not-loaded" not in doc["data"]["rates"]["codes"]


def test_broken_root_skill_does_not_pull_other_checkout_files_into_the_report(tmp_path):
    """A broken SKILL.md at the checkout root scans only itself: the files beside it belong to the checkout."""
    (tmp_path / "SKILL.md").write_text(BROKEN, encoding="utf-8")
    (tmp_path / "notes.md").write_text("hi " + chr(0x202E) + " x\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("hi " + chr(0x200B) + " x\n", encoding="utf-8")
    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review is not None and [f.code for f in review.findings] == ["frontmatter-parse"]


def test_missing_description_skill_with_clean_files_gets_no_review(tmp_path):
    """A skill that fails to load with clean bundled files keeps only the loader's error: no review is added."""
    (tmp_path / "skills" / "bad").mkdir(parents=True)
    (tmp_path / "skills" / "bad" / "SKILL.md").write_text("---\nname: bad\n---\nbody\n", encoding="utf-8")
    (tmp_path / "skills" / "bad" / "notes.md").write_text("hi there\n", encoding="utf-8")
    assert review_skills(read_skill_manifest(tmp_path)) == [None]
