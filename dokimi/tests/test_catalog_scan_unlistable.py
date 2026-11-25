# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1103: ``skill_dir_digest`` is None for a skill directory with a subdirectory it cannot list, not a digest of a smaller tree.

``skill_sha256`` of a catalog row is None then.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from dokimi import __version__
from dokimi.commands.catalog_scan import _load_prior, skill_dir_digest
from dokimi.digest import tree_digest


def _skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    (skill / "sub").mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: skill\ndescription: d\n---\nbody\n", encoding="utf-8")
    (skill / "sub" / "b.txt").write_text("b", encoding="utf-8")
    return skill


def _entry(skill: Path) -> SimpleNamespace:
    return SimpleNamespace(loaded=False, skill=None, path=skill)


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lists a mode 111 directory")
def test_a_skill_with_a_subdirectory_that_cannot_be_listed_has_no_digest(tmp_path: Path):
    skill = _skill(tmp_path)
    complete = skill_dir_digest(_entry(skill))
    assert complete is not None
    (skill / "sub").chmod(0o111)
    try:
        assert skill_dir_digest(_entry(skill)) is None
    finally:
        (skill / "sub").chmod(0o700)
    assert skill_dir_digest(_entry(skill)) == complete


@pytest.mark.parametrize(("digest", "kept"), [(None, False), ("abc", True)])
def test_resume_keeps_a_prior_row_only_when_its_digest_is_a_digest(tmp_path: Path, digest: str | None, kept: bool):
    row = {"name": "skill", "relpath": "skill", "state": "ok", "error": None, "findings": [], "content_sha256": None,
           "skill_sha256": digest, "listing_chars": 1, "listing": None, "listing_name": "skill", "ignored": [],
           "dokimi_version": __version__}
    jsonl = tmp_path / "scan.jsonl"
    jsonl.write_text(json.dumps(row) + "\n", encoding="utf-8")
    rows, dirty = _load_prior(jsonl, {"skill": digest}, frozenset())
    assert (rows == [row], dirty) == (kept, not kept)


def test_a_listable_skill_digests_as_the_non_strict_walk_did(tmp_path: Path):
    skill = _skill(tmp_path)
    (skill / "eval").mkdir()
    (skill / "eval" / "out.jsonl").write_text("{}", encoding="utf-8")
    out = (skill / "eval" / "out.jsonl").resolve()
    assert skill_dir_digest(_entry(skill), [out]) == tree_digest(skill, exclude=["eval/out.jsonl"], tracked={})
    assert skill_dir_digest(_entry(skill)) == tree_digest(skill, tracked={})
