# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for scripts/stack_health.py's merge check, against a real temporary git repo."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "stack_health.py"
_spec = importlib.util.spec_from_file_location("stack_health", _SCRIPT)
assert _spec and _spec.loader
sh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sh)


def _run(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo: Path, text: str, msg: str) -> str:
    (repo / "f.txt").write_text(text, encoding="utf-8")
    _run(repo, "add", "f.txt")
    _run(repo, "commit", "-q", "-m", msg)
    return _run(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """main plus PR heads as the script fetches them: #1 clean, #2 conflicting, #3 unfetched."""
    r = tmp_path / "repo"
    r.mkdir()
    _run(r, "init", "-q", "-b", "main")
    _run(r, "config", "user.email", "t@example.com")
    _run(r, "config", "user.name", "t")
    _run(r, "config", "commit.gpgsign", "false")
    root = _commit(r, "a\nb\nc\n", "root")
    main = _commit(r, "A\nb\nc\n", "main moves line 1")
    _run(r, "checkout", "-q", root)
    clean = _commit(r, "a\nb\nC\n", "pr 1 edits line 3")
    _run(r, "checkout", "-q", root)
    conflict = _commit(r, "X\nb\nc\n", "pr 2 edits line 1")
    _run(r, "update-ref", "refs/remotes/origin/main", main)
    _run(r, "update-ref", "refs/remotes/pull/1", clean)
    _run(r, "update-ref", "refs/remotes/pull/2", conflict)
    monkeypatch.chdir(r)
    return r


def _has_merge_tree_write_tree() -> bool:
    out = subprocess.run(["git", "merge-tree", "-h"], capture_output=True, text=True)
    return "--write-tree" in out.stdout + out.stderr


pytestmark = pytest.mark.skipif(not _has_merge_tree_write_tree(),
                                reason="git >= 2.38 needed for merge-tree --write-tree")


def test_clean_and_conflicting_heads(repo):
    assert sh._merges_cleanly("main", 1) is True
    assert sh._merges_cleanly("main", 2) is False


def test_an_unfetched_head_is_unknown_not_a_conflict(repo):
    # merge-tree itself exits 1 here, the same code as a conflict.
    r = subprocess.run(["git", "merge-tree", "--write-tree", "origin/main", "refs/remotes/pull/3"],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert sh._merges_cleanly("main", 3) is None


def test_a_pipe_in_a_title_stays_inside_its_cell(repo, monkeypatch, capsys):
    pr = {"number": 1, "title": "a | b", "draft": False, "base": {"ref": "main"},
          "head": {"ref": "b1", "repo": {"full_name": "o/r"}}}
    monkeypatch.setattr(sh, "_api", lambda url, token: [pr] if "/pulls?" in url else [])
    real_git = sh._git
    monkeypatch.setattr(sh, "_git", lambda *a, check=True: (
        subprocess.CompletedProcess(a, 0, "", "") if a[0] == "fetch" else real_git(*a, check=check)))
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    assert sh.main(["--repo", "o/r"]) == 0
    row = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("| #1"))
    assert row.replace(r"\|", "").count("|") == 8  # the table's 7 columns
    assert "| yes |" in row
