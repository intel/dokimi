# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi.utils.tree_digest: the one tree hash the build context and the repo config share."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dokimi.utils import tree_digest

symlinks = pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_the_same_content_at_another_path_digests_the_same(tmp_path):
    a = _write(tmp_path / "a", {"SKILL.md": "x\n", "scripts/run.py": "print(1)\n"})
    b = _write(tmp_path / "b", {"SKILL.md": "x\n", "scripts/run.py": "print(1)\n"})
    assert tree_digest(a) == tree_digest(b)


def test_a_byte_a_new_file_or_a_rename_each_moves_the_digest(tmp_path):
    root = _write(tmp_path / "t", {"SKILL.md": "x\n", "a.txt": "1\n"})
    base = tree_digest(root)
    (root / "a.txt").write_text("2\n", encoding="utf-8")
    edited = tree_digest(root)
    assert edited != base
    (root / "b.txt").write_text("new\n", encoding="utf-8")
    assert tree_digest(root) not in (base, edited)
    before_rename = tree_digest(root)
    (root / "a.txt").rename(root / "a2.txt")  # same sort position, so only the name can move the digest
    assert tree_digest(root) != before_rename


@pytest.mark.skipif(os.name == "nt", reason="chmod does not change the executable bit on Windows")
def test_the_executable_bit_is_part_of_the_digest(tmp_path):
    root = _write(tmp_path / "t", {"run.sh": "echo\n"})
    (root / "run.sh").chmod(0o644)
    before = tree_digest(root)
    (root / "run.sh").chmod(0o755)
    assert tree_digest(root) != before


@symlinks
def test_a_directory_symlink_root_digests_its_target_not_the_tree_under_it(tmp_path):
    one = _write(tmp_path / "one", {"SKILL.md": "x\n"})
    two = _write(tmp_path / "two", {"SKILL.md": "x\n"})
    link = tmp_path / "link"
    os.symlink(one, link, target_is_directory=True)
    before = tree_digest(link)
    link.unlink()
    os.symlink(two, link, target_is_directory=True)
    assert tree_digest(link) != before


@symlinks
def test_a_dangling_root_symlink_digests_its_target_text(tmp_path):
    link = tmp_path / "link"
    os.symlink("missing-a", link)
    before = tree_digest(link)
    link.unlink()
    os.symlink("missing-b", link)
    assert tree_digest(link) != before


def test_exclude_leaves_out_a_subtree_and_nothing_else(tmp_path):
    full = _write(tmp_path / "full", {"SKILL.md": "x\n", "evals/q.json": "[]\n", "evalsx/keep.txt": "k\n"})
    bare = _write(tmp_path / "bare", {"SKILL.md": "x\n", "evalsx/keep.txt": "k\n"})
    assert tree_digest(full, exclude=["evals"]) == tree_digest(bare)
    assert tree_digest(full) != tree_digest(bare)


def test_exclude_takes_a_file_path_too(tmp_path):
    full = _write(tmp_path / "full", {"a.txt": "a\n", "b/notes.md": "n\n"})
    bare = _write(tmp_path / "bare", {"a.txt": "a\n"})
    (bare / "b").mkdir()  # the directory entry stays; only the excluded file goes
    assert tree_digest(full, exclude=["b/notes.md"]) == tree_digest(bare)


def test_a_file_root_digests_as_itself(tmp_path):
    one = _write(tmp_path / "one", {"q.json": "[1]\n"})
    two = _write(tmp_path / "two", {"elsewhere.json": "[1]\n"})
    assert tree_digest(one / "q.json") == tree_digest(two / "elsewhere.json")
    assert tree_digest(one / "q.json") != tree_digest(_write(tmp_path / "three", {"q.json": "[2]\n"}) / "q.json")


def test_the_digest_is_bare_hex(tmp_path):
    digest = tree_digest(_write(tmp_path / "t", {"a": "a\n"}))
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


@pytest.mark.skipif(os.name == "nt", reason="POSIX file names and link targets are bytes")
def test_an_undecodable_name_and_link_target_digest_without_error(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    fd = os.open(os.fsencode(root) + b"/bad-\xff", os.O_CREAT | os.O_WRONLY, 0o644)
    os.close(fd)
    os.symlink(b"target-\xff", os.fsencode(root / "link"))
    first = tree_digest(root)
    other = tmp_path / "other"
    other.mkdir()
    fd = os.open(os.fsencode(other) + b"/bad-\xfe", os.O_CREAT | os.O_WRONLY, 0o644)
    os.close(fd)
    os.symlink(b"target-\xff", os.fsencode(other / "link"))
    assert first != tree_digest(other)
