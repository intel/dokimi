# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The public per-skill review, ``review_skill_dir(path) -> SkillReview``."""

from __future__ import annotations

from pathlib import Path

import pytest

import dokimi.external as external
from dokimi.external import SkillReview, review_skill_dir


def _write(skill_dir: Path, text: str) -> Path:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    return skill_dir


def test_returns_the_review_of_the_skill_in_that_directory(tmp_path):
    skill_dir = _write(tmp_path / "solo", "---\nname: solo\ndescription: Review pull requests.\n---\n# Solo\n")

    review = review_skill_dir(skill_dir)

    assert isinstance(review, SkillReview)
    assert [f.code for f in review.findings] == ["description-no-when"]
    assert review.state == "warn"
    assert review.content_sha256 is not None


def test_a_skill_that_does_not_load_is_an_error_finding_with_the_loader_message(tmp_path):
    skill_dir = _write(tmp_path / "nodesc", "---\nname: nodesc\n---\n# No description\n")

    review = review_skill_dir(skill_dir)

    assert review.state == "error"
    [finding] = review.findings
    assert finding.code == "not-loaded"
    assert "description" in finding.message


def test_a_nested_skill_is_not_reviewed_as_part_of_its_outer_skill(tmp_path):
    outer = _write(tmp_path / "outer", "---\nname: outer\ndescription: Use when outer work runs.\n---\n# Outer\n")
    _write(outer / "inner", "---\nname: inner\n---\nno description here\n")

    review = review_skill_dir(outer)

    assert review.state == "ok"
    assert review.findings == []


def test_a_link_out_of_the_skill_directory_is_a_warning(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "notes.md").write_text("notes\n", encoding="utf-8")
    skill_dir = _write(
        tmp_path / "skill",
        "---\nname: skill\ndescription: Use when a skill links out.\n---\nSee [notes](../shared/notes.md).\n",
    )

    codes = [f.code for f in review_skill_dir(skill_dir).findings]

    assert codes == ["link-escapes-skill"]


def test_a_directory_with_no_skill_md_is_a_value_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ValueError, match="no SKILL.md"):
        review_skill_dir(empty)


def test_a_path_that_is_not_a_directory_is_a_value_error(tmp_path):
    with pytest.raises(ValueError, match="not a skill directory"):
        review_skill_dir(tmp_path / "missing")


def test_the_api_is_exported_from_the_package():
    assert "review_skill_dir" in external.__all__
    assert external.review_skill_dir is review_skill_dir
