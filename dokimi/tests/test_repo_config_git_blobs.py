# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""skill_sha and suite_hash hash what git stores: blob ids, no ignored paths, no nested ``.git``."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from dokimi.repo_config import load_repo_config
from dokimi.utils import git_tree, tree_digest

HEADER = "schema_version: dokimi_config.v1\n"
CONFIG = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: skills/a/evals\n"
FILES = {"skills/a/SKILL.md": "a\nb\n", "skills/a/evals/t/test.bat": "echo a\necho b\n", "skills/a/evals/q.json": "[]\n"}


def _git(root: Path, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def _repo(root: Path, files: dict[str, str]) -> Path:
    """A committed git repository at ``root`` holding ``files`` (LF) and the config; returns its resolved path."""
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    files = {".github/dokimi/config.yaml": CONFIG, ".gitignore": "__pycache__/\n.DS_Store\n", **files}
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root.resolve()


def _hashes(root: Path) -> tuple[str, str]:
    subject = load_repo_config(root).subjects()[0]
    return subject.skill_sha, subject.suite_hash


def test_ignored_files_do_not_move_skill_sha_or_suite_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    for rel in ("skills/a/__pycache__/x.pyc", "skills/a/evals/__pycache__/y.pyc", "skills/a/.DS_Store"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"ignored\n")
    assert _hashes(root) == before
    assert subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True,
                          text=True, check=True).stdout == ""


def test_a_nested_git_directory_does_not_move_skill_sha(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    _git(root / "skills" / "a", "init", "-q")  # a nested repository's own .git holds no content of the skill
    assert _hashes(root)[0] == before[0]


def test_a_committed_lf_to_crlf_change_moves_suite_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    bat = root / "skills/a/evals/t/test.bat"
    bat.write_bytes(b"echo a\r\necho b\r\n")
    _git(root, "commit", "-qam", "crlf")
    after = _hashes(root)
    assert after[1] != before[1]  # git sees the change, so the suite hash must


def test_a_crlf_checkout_with_autocrlf_hashes_as_the_lf_clone(tmp_path):
    src = _repo(tmp_path / "src", FILES)
    clone = tmp_path / "clone"
    subprocess.run(["git", "-c", "core.autocrlf=true", "clone", "-q", str(src), str(clone)], check=True,
                   capture_output=True)
    _git(clone, "config", "core.autocrlf", "true")
    assert b"\r\n" in (clone / "skills/a/evals/t/test.bat").read_bytes()  # the checkout really is CRLF
    assert _hashes(clone.resolve()) == _hashes(src)


def test_a_file_that_is_not_in_git_is_hashed_as_its_bytes(tmp_path):
    # outside a work tree nothing is ignored and no line end is folded: the fallback is the bytes on disk
    lf = tmp_path / "lf"
    crlf = tmp_path / "crlf"
    for root, newline in ((lf, b"\n"), (crlf, b"\r\n")):
        (root / "skills/a/evals").mkdir(parents=True)
        (root / ".github/dokimi").mkdir(parents=True)
        (root / ".github/dokimi/config.yaml").write_text(CONFIG)
        (root / "skills/a/SKILL.md").write_bytes(b"a" + newline)
        (root / "skills/a/evals/q.json").write_bytes(b"[]" + newline)
    assert _hashes(lf) != _hashes(crlf)
    before = _hashes(lf)
    (lf / "skills/a/__pycache__").mkdir()
    (lf / "skills/a/__pycache__/x.pyc").write_bytes(b"x")
    assert _hashes(lf) != before


def test_a_new_untracked_file_moves_the_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    (root / "skills/a/notes.md").write_text("not ignored\n")
    assert _hashes(root)[0] != before[0]


def test_a_committed_crlf_file_hashes_the_same_in_an_autocrlf_clone(tmp_path):
    src = _repo(tmp_path / "src", {**FILES, "skills/a/evals/t/test.bat": "echo a\r\necho b\r\n"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(src), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "core.autocrlf", "true")
    assert _hashes(clone.resolve()) == _hashes(src)  # git keeps the committed CRLF blob in both clones


def test_an_ignored_directory_above_the_root_does_not_hide_its_files(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q")
    (outer / ".gitignore").write_text("build/\n")
    root = outer / "build" / "skills-copy"
    (root / "a").mkdir(parents=True)
    (root / "a" / "SKILL.md").write_text("a\n")
    _git(outer, "add", "-A")
    _git(outer, "commit", "-q", "-m", "init")
    before = tree_digest(root, git=git_tree(root))
    (root / "a" / "SKILL.md").write_text("b\n")
    assert tree_digest(root, git=git_tree(root)) != before  # build/ is git's, not the walk's: SKILL.md still counts


def test_a_touched_crlf_file_keeps_its_hash_in_an_autocrlf_clone(tmp_path):
    src = _repo(tmp_path / "src", {**FILES, "skills/a/evals/t/test.bat": "echo a\r\necho b\r\n"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(src), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "core.autocrlf", "true")
    later = time.time() + 60  # the same bytes, a clearly newer mtime (git would then compare the content)
    os.utime(clone / "skills/a/evals/t/test.bat", (later, later))
    assert _hashes(clone.resolve()) == _hashes(src)


def test_an_edit_under_skip_worktree_moves_the_hash(tmp_path):
    root = _repo(tmp_path / "r", FILES)
    before = _hashes(root)
    _git(root, "update-index", "--skip-worktree", "skills/a/SKILL.md")
    (root / "skills/a/SKILL.md").write_text("changed\n")
    assert _hashes(root) != before  # git does not look at a skip-worktree file; the hash must still read the disk


def test_a_rename_in_the_work_tree_column_hashes_the_work_tree(tmp_path):
    root = _repo(tmp_path / "r", {**FILES, "README.md": "readme\n"})
    (root / "README.md").rename(root / "README2.md")  # an origin path starting with R must not swallow the next entry
    (root / "skills/a/evals/q.json").rename(root / "skills/a/evals/n.json")
    _git(root, "add", "-N", "README2.md", "skills/a/evals/n.json")  # the index holds n.json as an empty blob
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "-uno"], capture_output=True,
                            check=True).stdout
    assert b" R skills/a/evals/n.json\0skills/a/evals/q.json\0" in status
    dirty = _hashes(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "rename")
    assert dirty == _hashes(root)

