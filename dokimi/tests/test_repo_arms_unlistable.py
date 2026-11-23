# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1104: ``tree_sha256`` raises for a subdirectory it cannot list, not a digest of a smaller tree."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from dokimi.digest_entries import listed_entries
from dokimi.repos.arms import tree_sha256


def _skill(tmp_path: Path) -> Path:
    skill = tmp_path / "skill"
    (skill / "sub" / "deeper").mkdir(parents=True)
    (skill / "SKILL.md").write_bytes(b"skill\n")
    (skill / "sub" / "b.txt").write_text("b", encoding="utf-8")
    (skill / "sub" / "deeper" / "c.txt").write_text("c", encoding="utf-8")
    return skill


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root lists a mode 111 directory")
@pytest.mark.parametrize("parts", [(), ("sub",), ("sub", "deeper")])  # the root itself, a subdirectory, one below a listable one
def test_a_skill_with_a_directory_that_cannot_be_listed_raises(tmp_path: Path, parts: tuple[str, ...]):
    skill = _skill(tmp_path)
    unlistable = skill.joinpath(*parts)
    complete = tree_sha256(skill)
    unlistable.chmod(0o111)
    try:
        with pytest.raises(PermissionError, match=unlistable.name):
            tree_sha256(skill)
    finally:
        unlistable.chmod(0o700)
    assert tree_sha256(skill) == complete


def test_a_listable_skill_digests_as_the_rglob_walk_did(tmp_path: Path):
    skill = _skill(tmp_path)
    expected = hashlib.sha256()
    for rel, data in (("SKILL.md", b"skill\n"), ("sub/b.txt", b"b"), ("sub/deeper/c.txt", b"c")):
        expected.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    assert tree_sha256(skill) == expected.hexdigest()


def test_listed_entries_are_those_of_rglob_with_a_directory_link_not_followed(tmp_path: Path):
    skill = _skill(tmp_path)
    try:
        (skill / "link").symlink_to(skill / "sub", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    (skill / "dangling").symlink_to(skill / "missing")
    assert sorted(listed_entries(skill)) == sorted(skill.rglob("*"))
