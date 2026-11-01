# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""subject.distribution_digest: the digest a .well-known/agent-skills index carries."""

from __future__ import annotations

import hashlib
from pathlib import Path

from dokimi.subjects.scorecard import distribution_digest


def test_distribution_digest_matches_a_well_known_skill_md_entry(tmp_path: Path):
    # agentskills PR #254: a skill-md index entry carries sha256 of the SKILL.md bytes it serves.
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_bytes(b"---\nname: s\n---\nbody\n")
    expected = "sha256:" + hashlib.sha256(b"---\nname: s\n---\nbody\n").hexdigest()

    assert distribution_digest("skill", base=tmp_path) == expected
    assert distribution_digest("local:skill/SKILL.md", base=tmp_path) == expected

    # With resources the skill is served as an archive, whose bytes a checkout cannot predict.
    (skill / "ref.md").write_text("more", encoding="utf-8")
    assert distribution_digest("skill", base=tmp_path) is None
    assert distribution_digest("skill/SKILL.md", base=tmp_path) == expected
    # A ref that is not a local path has no served bytes to hash.
    assert distribution_digest("context7:/org/lib", base=tmp_path) is None
    assert distribution_digest(None, base=tmp_path) is None
    # Only SKILL.md is served as a skill-md entry; another file has no index digest.
    (tmp_path / "notes.md").write_text("x", encoding="utf-8")
    assert distribution_digest("notes.md", base=tmp_path) is None


def test_a_ref_with_a_scheme_gets_no_digest_even_when_a_directory_collides(tmp_path: Path):
    colliding = tmp_path / "context7:" / "org" / "lib"
    colliding.mkdir(parents=True)
    (colliding / "SKILL.md").write_bytes(b"body")

    assert distribution_digest("context7:/org/lib", base=tmp_path) is None


def test_the_scorecard_carries_the_distribution_digest(tmp_path: Path, monkeypatch):
    from dokimi.artifacts import validate_artifact
    from dokimi.subjects import build_subject_scorecard, load_subject

    (tmp_path / "skill").mkdir()
    (tmp_path / "skill" / "SKILL.md").write_bytes(b"body")
    (tmp_path / "subject.toml").write_text(
        '[subject]\nid = "s"\nkind = "skill"\nref = "skill"\n\n'
        '[suite]\nproducts = ["p"]\nquestions = ["q.json"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    scorecard = build_subject_scorecard(load_subject(tmp_path / "subject.toml"), awareness_runs=[], out_dir=tmp_path)

    assert scorecard["subject"]["distribution_digest"] == "sha256:" + hashlib.sha256(b"body").hexdigest()
    validate_artifact("subject_scorecard", scorecard)
