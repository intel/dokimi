# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``catalog check --per-skill`` and ``review_skill_dir``: a skill reviewed alone has no cross-catalog check."""

from __future__ import annotations

import json
from pathlib import Path

from dokimi.cli import build_parser
from dokimi.external import read_skill_manifest, review_skill_dir, review_skills

CROSS_CODES = {"duplicate", "name-conflict", "mirror-drift", "description-near-duplicate"}


def _skill(root: Path, rel: str, name: str, description: str, body: str = "# Body\n") -> Path:
    skill_dir = root / rel
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8"
    )
    return skill_dir


def _two_plugins_with_one_skill_name(root: Path) -> None:
    """Two unrelated skills that share a name and an overlapping description, in different plugins."""
    _skill(root, "plugins/a/review", "review", "Use when reviewing pull request diffs for bugs.")
    _skill(root, "plugins/b/review", "review", "Use when reviewing pull request diffs for style.",
           body="# Other body\n")


def _codes_of_row(row: dict) -> set[str]:
    return {f["code"] for f in row["findings"]}


def test_a_default_scan_fires_the_cross_checks_between_unrelated_skills(tmp_path):
    _two_plugins_with_one_skill_name(tmp_path)

    codes = [{f.code for f in r.findings} for r in review_skills(read_skill_manifest(tmp_path))]
    assert all("name-conflict" in c for c in codes)
    assert all("description-near-duplicate" in c for c in codes)


def test_review_skill_dir_of_each_skill_has_none_of_the_cross_checks(tmp_path):
    _two_plugins_with_one_skill_name(tmp_path)

    for skill_dir in (tmp_path / "plugins" / "a" / "review", tmp_path / "plugins" / "b" / "review"):
        assert CROSS_CODES.isdisjoint({f.code for f in review_skill_dir(skill_dir).findings})


def test_per_skill_keeps_the_checks_that_are_about_one_skill(tmp_path):
    # "Use when" is missing: a per-skill finding, so it must survive the per-skill mode.
    _skill(tmp_path, "skills/solo", "solo", "Review pull requests for bugs.")

    review = review_skill_dir(tmp_path / "skills" / "solo")
    assert [f.code for f in review.findings] == ["description-no-when"]


def test_cli_per_skill_drops_name_conflict_and_the_default_scan_keeps_it(tmp_path, capsys):
    _two_plugins_with_one_skill_name(tmp_path)
    parser = build_parser()

    args = parser.parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills",
                              "--format", "json"])
    args.func(args)
    default_rows = json.loads(capsys.readouterr().out)["skills"]
    assert any("name-conflict" in _codes_of_row(row) for row in default_rows)

    args = parser.parse_args(["catalog", "check", "--root", str(tmp_path), "--kind", "skills",
                              "--format", "json", "--per-skill"])
    args.func(args)
    per_skill_rows = json.loads(capsys.readouterr().out)["skills"]
    assert len(per_skill_rows) == 2
    assert all(CROSS_CODES.isdisjoint(_codes_of_row(row)) for row in per_skill_rows)
