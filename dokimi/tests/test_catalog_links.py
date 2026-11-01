# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Link targets, reference definitions, fenced blocks and directory names in ``dokimi.external.validate``.

Split from ``test_catalog_validate.py``, which is over the file-length cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dokimi.external import read_skill_manifest, review_skills


def _skill(root: Path, rel: str, name: str | None = None, description: str = "Use for things.",
           extra: str = "", body: str = "# Body\n") -> Path:
    """Write ``<root>/<rel>/SKILL.md``; ``name`` None leaves the key out of the frontmatter."""
    skill_dir = root / rel
    skill_dir.mkdir(parents=True, exist_ok=True)
    head = f"name: {name}\n" if name is not None else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\n{head}description: {description}\n{extra}---\n{body}", encoding="utf-8")
    return skill_dir


def _reviews_by_name(root: Path) -> dict:
    entries = read_skill_manifest(root)
    return {e.name: r for e, r in zip(entries, review_skills(entries), strict=True)}


def _codes(review) -> list[str]:
    return [f.code for f in review.findings]


def test_a_link_destination_with_parentheses_to_an_existing_file_is_not_missing(tmp_path):
    skill_dir = _skill(tmp_path / "skills", "parens", name="parens", description="Use for parens.",
                       body="See [the guide](references/guide(v2).md) and [a](references/a(b)(c).md).\n")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "guide(v2).md").write_text("# Guide\n", encoding="utf-8")
    (skill_dir / "references" / "a(b)(c).md").write_text("# A\n", encoding="utf-8")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_link_destination_with_parentheses_to_a_missing_file_names_the_whole_path(tmp_path):
    _skill(tmp_path / "skills", "parens-missing", name="parens-missing", description="Use for parens.",
           body="See [the guide](references/guide(v2).md).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'references/guide(v2).md'" in review.findings[0].message


@pytest.mark.parametrize("exists", [True, False])
def test_angle_bracket_destination_with_spaces_is_checked(tmp_path, exists):
    (tmp_path / "shared").mkdir()
    if exists:
        (tmp_path / "shared" / "my notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "spaced-link", name="spaced-link", description="Use for spaced link.",
           body="See [notes](<../../shared/my notes.md>).\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == (["link-escapes-skill"] if exists else ["link-missing"])


@pytest.mark.parametrize("exists", [True, False])
def test_reference_definition_with_destination_on_the_next_line_is_checked(tmp_path, exists):
    (tmp_path / "shared").mkdir()
    if exists:
        (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "next-line", name="next-line", description="Use for next line.",
           body="See [notes][n].\n\n[n]:\n  ../../shared/notes.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == (["link-escapes-skill"] if exists else ["link-missing"])


def test_a_tilde_line_inside_a_backtick_fence_does_not_close_it(tmp_path):
    _skill(tmp_path / "skills", "fences", name="fences", description="Use for fences.",
           body="```markdown\n~~~\n[example](missing.md)\n```\n[real](also-missing.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]
    assert "'also-missing.md'" in review.findings[0].message


def test_a_closing_fence_shorter_than_the_opening_one_does_not_close_it(tmp_path):
    _skill(tmp_path / "skills", "fence-run", name="fence-run", description="Use for fence run.",
           body="````\n```\n[example](missing.md)\n````\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []


def test_a_fence_indented_four_spaces_does_not_open_a_block(tmp_path):
    _skill(tmp_path / "skills", "indented", name="indented", description="Use for indented.",
           body="    ```\n[a](gone.md)\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert _codes(review) == ["link-missing"]


def test_skills_named_through_a_subdirectory_dot_dot_are_not_mirror_drift(tmp_path):
    # "skills/alpha/sub/.." is the skill directory "alpha": two such skills are not copies of each other.
    _skill(tmp_path / "skills", "alpha", name="alpha", description="Use for alpha work.", body="# Alpha\n")
    _skill(tmp_path / "skills", "beta", name="beta", description="Use for beta work.", body="# Beta\n")
    (tmp_path / "skills" / "alpha" / "sub").mkdir()
    (tmp_path / "skills" / "beta" / "sub").mkdir()
    (tmp_path / "skills.yaml").write_text(
        "skills:\n"
        "  - name: alpha\n    catalog_path: skills/alpha/sub/..\n"
        "  - name: beta\n    catalog_path: skills/beta/sub/..\n",
        encoding="utf-8")

    reviews = _reviews_by_name(tmp_path)
    assert _codes(reviews["alpha"]) == []
    assert _codes(reviews["beta"]) == []


def test_a_reference_label_before_a_fence_does_not_take_a_destination_after_it(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "notes.md").write_text("# Notes\n", encoding="utf-8")
    _skill(tmp_path / "skills", "label-fence", name="label-fence", description="Use for label fence.",
           body="See [notes][n].\n\n[n]:\n```\ncode\n```\n../../shared/notes.md\n")

    [review] = review_skills(read_skill_manifest(tmp_path))
    assert review.findings == []
