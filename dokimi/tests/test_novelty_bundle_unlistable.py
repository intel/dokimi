# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1106: ``load_bundle`` raises for a skill subdirectory it cannot list, not a bundle of a smaller skill."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dokimi.novelty.bundle import load_bundle


def _skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: skill\ndescription: d\n---\nUse the references.\n", encoding="utf-8")
    (skill / "references" / "a.md").write_text("alpha beta gamma\n", encoding="utf-8")
    return skill


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lists a mode 111 directory")
@pytest.mark.parametrize("include_resources", [True, False])
def test_a_subdirectory_that_cannot_be_listed_raises(tmp_path: Path, include_resources: bool):
    skill = _skill(tmp_path)
    (skill / "references").chmod(0o111)
    try:
        with pytest.raises(PermissionError, match="references"):
            load_bundle(f"skill:{skill}", include_resources=include_resources)
    finally:
        (skill / "references").chmod(0o700)
    assert [f.path for f in load_bundle(f"skill:{skill}", include_resources=True).files] == ["SKILL.md", "references/a.md"]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lists a mode 111 directory")
@pytest.mark.parametrize("name", [".git", "venv", "node_modules"])
@pytest.mark.parametrize("include_resources", [True, False])
def test_a_directory_that_is_not_content_is_not_listed(tmp_path: Path, name: str, include_resources: bool):
    skill = _skill(tmp_path)
    (skill / name).mkdir()
    (skill / name).chmod(0o111)
    try:
        bundle = load_bundle(f"skill:{skill}", include_resources=include_resources)
    finally:
        (skill / name).chmod(0o700)
    assert [f.path for f in bundle.files] == (["SKILL.md", "references/a.md"] if include_resources else ["SKILL.md"])


def test_a_file_named_like_a_skipped_directory_is_still_read(tmp_path: Path):
    skill = _skill(tmp_path)
    (skill / "references" / "build").write_text("one two\n", encoding="utf-8")
    assert [f.path for f in load_bundle(f"skill:{skill}", include_resources=True).files] == [
        "SKILL.md", "references/a.md", "references/build"]


def test_a_listable_skill_keeps_its_files_digest_and_words_on_disk(tmp_path: Path):
    skill = _skill(tmp_path)
    (skill / "references" / "deeper").mkdir()
    (skill / "references" / "deeper" / "b.md").write_text("delta\n", encoding="utf-8")
    bundle = load_bundle(f"skill:{skill}", include_resources=True)
    assert [(f.path, f.role) for f in bundle.files] == [
        ("SKILL.md", "entry_point"), ("references/a.md", "reference"), ("references/deeper/b.md", "reference")]
    assert bundle.resource_words_on_disk == 4


def test_a_symlink_named_like_a_skipped_directory_is_excluded_as_a_symlink(tmp_path: Path):
    skill = _skill(tmp_path)
    (skill / "venv").symlink_to("references")
    bundle = load_bundle(f"skill:{skill}", include_resources=True)
    assert [(row["path"], row["reason"]) for row in bundle.excluded] == [("venv", "symlink")]
