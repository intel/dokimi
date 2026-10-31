# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi.utils.tree_digest: the one tree hash the build context and the repo config share."""

from __future__ import annotations

from pathlib import Path

from dokimi.utils import tree_digest


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
    (root / "a.txt").rename(root / "c.txt")
    assert tree_digest(root) != base


def test_the_executable_bit_is_part_of_the_digest(tmp_path):
    root = _write(tmp_path / "t", {"run.sh": "echo\n"})
    before = tree_digest(root)
    (root / "run.sh").chmod(0o755)
    assert tree_digest(root) != before


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
