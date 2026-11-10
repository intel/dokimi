# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A path git prints with a byte that is not UTF-8 (#550): Windows Python's ``os.fsdecode`` raises on it, so the
readers of git's ``-z`` listings decode with ``process_io.path_text``, which puts U+FFFD there. The tests decode
as Windows does (UTF-8, ``surrogatepass``)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dokimi import process_io, utils
from dokimi.external import catalog_git

BAD = b"l\xffint/SKILL.md"
REPLACED = "l�int/SKILL.md"


def _git(root: Path, *args: str | bytes, stdin: bytes | None = None) -> bytes:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env,
                          input=stdin).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    """``skill/SKILL.md`` committed, and ``l\\xffint/SKILL.md`` in the base commit and the index with no file on disk."""
    root = tmp_path / "repo"
    (root / "skill").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "skill" / "SKILL.md").write_text("one\n", encoding="utf-8")
    _git(root, "add", "-A")
    blob = _git(root, "hash-object", "-w", "--stdin", stdin=b"odd\n").strip().decode()
    _git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},".encode() + BAD)
    _git(root, "commit", "-q", "-m", "base")
    (root / "skill" / "SKILL.md").write_text("two\n", encoding="utf-8")
    _git(root, "add", "skill/SKILL.md")  # not -a: that would stage the deletion of the odd name
    _git(root, "commit", "-q", "-m", "edit")
    return root.resolve()


@pytest.fixture
def windows_decoding(monkeypatch):
    """``os.fsdecode`` as Windows Python does it, on a platform that reports itself as Windows."""
    monkeypatch.setattr(os, "fsdecode", lambda raw: os.fspath(raw) if isinstance(raw, str) else raw.decode("utf-8", "surrogatepass"))
    monkeypatch.setattr(process_io, "_POSIX", False)


def test_the_fixture_decodes_as_windows_does(windows_decoding):
    with pytest.raises(UnicodeDecodeError):
        os.fsdecode(BAD)


def test_git_tree_holds_a_path_that_is_not_utf8(repo, windows_decoding):
    tree = utils.git_tree(repo / "skill")
    assert tree is not None
    assert REPLACED in tree.tracked and "skill/SKILL.md" in tree.tracked
    assert "skill/SKILL.md" in tree.indexed


def test_tree_digest_with_git_reads_an_index_with_a_path_that_is_not_utf8(repo, windows_decoding):
    digest = utils.tree_digest(repo / "skill", git=True)
    assert len(digest) == 64 and digest == utils.tree_digest(repo / "skill", git=True)


def test_git_tree_holds_an_ignored_path_that_is_not_utf8(repo, windows_decoding):
    (repo / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    with open(os.fsencode(repo) + b"/skill/a\xff.tmp", "wb") as handle:
        handle.write(b"x")
    tree = utils.git_tree(repo / "skill")
    assert tree is not None and "skill/a�.tmp" in tree.ignored


def test_the_index_readers_decode_a_name_that_is_not_utf8(windows_decoding):
    assert utils._worktree_changed(b" M " + BAD + b"\0") == {REPLACED}
    assert utils._flagged(b"S " + BAD + b"\0H x\0") == {REPLACED}


def test_tracked_modes_keeps_a_path_that_is_not_utf8(repo, windows_decoding):
    assert utils._tracked_modes(repo, ".") == {REPLACED: False, "skill/SKILL.md": False}


def test_catalog_paths_report_a_path_that_is_not_utf8(repo, windows_decoding):
    listed = catalog_git._paths(catalog_git._git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD~1"))
    assert listed == [REPLACED, "skill/SKILL.md"]


def test_changed_from_a_base_with_a_path_that_is_not_utf8_checks_instead_of_failing(repo, windows_decoding):
    _source, paths, _untracked, _owners, reason = catalog_git.source_and_changes(repo, "HEAD~1", repo / "out")
    assert reason is None and paths == [REPLACED, "skill/SKILL.md"]  # the other is in the index with no file


@pytest.mark.skipif(os.name != "posix", reason="POSIX keeps the byte; Windows replaces it")
def test_a_posix_decoding_still_names_the_file_by_its_own_bytes(repo):
    listed = catalog_git._paths(catalog_git._git(repo, "ls-tree", "-r", "-z", "--name-only", "HEAD~1"))
    assert listed == [os.fsdecode(BAD), "skill/SKILL.md"]
