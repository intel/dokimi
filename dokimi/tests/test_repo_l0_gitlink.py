# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""L0 reads a submodule as a directory of the pinned tree, the way a checkout holds it."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from dokimi.repos import parse_manifest
from dokimi.repos.l0 import scan_repo
from dokimi.repos.source import GitTree

_GITMODULES = '[submodule "m"]\n\tpath = third_party/mod\n\turl = https://example.invalid/m.git\n'


def _manifest():
    return parse_manifest({
        "schema": "repo_manifest.v1", "id": "case", "source": {"url": "https://github.com/acme/case"},
        "eval_dir": "ev", "guidance": {"globs": ["**/AGENTS.md"], "also_scan": []},
        "entry_points": {"grade": "true"}, "tasks": "ev/tasks", "build_systems": [],
    })


def _tree_with_lone_gitlink(root, agents: str, gitlink: bool = True, gitmodules: str | None = _GITMODULES) -> GitTree:
    files = {"AGENTS.md": agents, "ev/tasks/t/task.json": "{}"}
    if gitmodules is not None:
        files[".gitmodules"] = gitmodules
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git = ["git", "-C", str(root)]
    link = [["update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},third_party/mod"]] if gitlink else []
    for args in (["init", "-q"], ["add", "-A"], *link,
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "g"]):
        subprocess.run([*git, *args], check=True, capture_output=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    return GitTree(root / ".git", sha)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_directory_holding_only_a_submodule_is_a_directory_to_l0(tmp_path):
    tree = _tree_with_lone_gitlink(tmp_path, "See `third_party/` and `third_party/mod/` for vendored code.\n")
    payload = scan_repo(tree, _manifest())
    claims = {c["claim"]: c["status"] for c in payload["claims"] if c["claim"].startswith("third_party")}
    assert claims == {"third_party/": "ok", "third_party/mod/": "unresolved"}  # the submodule's content is not held


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_stale_gitmodules_entry_does_not_invent_a_directory(tmp_path):
    tree = _tree_with_lone_gitlink(tmp_path, "See `third_party/` and `third_party/other.txt`.\n", gitlink=False)
    assert not tree.is_dir("third_party")
    payload = scan_repo(tree, _manifest())
    claims = {c["claim"]: c["status"] for c in payload["claims"] if c["claim"].startswith("third_party")}
    assert claims == {"third_party/": "unresolved", "third_party/other.txt": "unresolved"}  # no gitlink: no directory


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_submodule_without_a_gitmodules_entry_is_still_a_directory(tmp_path):
    tree = _tree_with_lone_gitlink(tmp_path, "See `third_party/` and `third_party/other.txt`.\n", gitmodules=None)
    payload = scan_repo(tree, _manifest())
    claims = {c["claim"]: c["status"] for c in payload["claims"] if c["claim"].startswith("third_party")}
    assert claims == {"third_party/": "ok", "third_party/other.txt": "fail"}  # the directory is there, the file is not


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_declared_path_that_leaves_the_tree_does_not_abort_the_scan(tmp_path):
    escaping = '[submodule "m"]\n\tpath = ../outside\n\turl = https://example.invalid/m.git\n'
    tree = _tree_with_lone_gitlink(tmp_path, "See `third_party/`.\n", gitlink=False, gitmodules=escaping)
    payload = scan_repo(tree, _manifest())
    claims = {c["claim"]: c["status"] for c in payload["claims"] if c["claim"].startswith("third_party")}
    assert claims == {"third_party/": "unresolved"}


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_file_inside_an_undeclared_submodule_is_unresolved(tmp_path):
    tree = _tree_with_lone_gitlink(tmp_path, "See `third_party/mod/docs.md`.\n", gitmodules=None)
    payload = scan_repo(tree, _manifest())
    claims = {c["claim"]: c["status"] for c in payload["claims"]}
    assert claims == {"third_party/mod/docs.md": "unresolved"}  # the submodule's content is not held


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.parametrize("gitmodules", [_GITMODULES, None], ids=["declared", "undeclared"])
def test_a_bare_submodule_name_is_unresolved_like_its_full_path(tmp_path, gitmodules):
    tree = _tree_with_lone_gitlink(tmp_path, "See `mod/`.\n", gitmodules=gitmodules)
    payload = scan_repo(tree, _manifest())
    claims = {c["claim"]: c["status"] for c in payload["claims"]}
    assert claims == {"mod/": "unresolved"}  # as `third_party/mod/`: the snapshot does not hold it


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_bare_name_that_also_matches_a_held_directory_still_resolves(tmp_path):
    (tmp_path / "docs" / "mod").mkdir(parents=True)
    (tmp_path / "docs" / "mod" / "x.md").write_text("x", encoding="utf-8")
    payload = scan_repo(_tree_with_lone_gitlink(tmp_path, "See `mod/`.\n"), _manifest())
    assert {c["claim"]: c["status"] for c in payload["claims"]} == {"mod/": "ok_basename"}


def test_a_symlink_loop_at_a_declared_path_does_not_abort_the_scan(tmp_path, monkeypatch):
    from dokimi.repos.source import WorkTree

    (tmp_path / "AGENTS.md").write_text("See `third_party/`.\n", encoding="utf-8")
    (tmp_path / ".gitmodules").write_text(_GITMODULES, encoding="utf-8")
    (tmp_path / "third_party").mkdir()
    (tmp_path / "third_party" / "x.txt").write_text("x", encoding="utf-8")
    real = WorkTree.is_dir

    def is_dir(self, path):
        if path == "third_party/mod":
            raise RuntimeError("Symlink loop")  # what Path.resolve raises before Python 3.13
        return real(self, path)

    monkeypatch.setattr(WorkTree, "is_dir", is_dir)
    payload = scan_repo(WorkTree(tmp_path), _manifest())
    assert {c["claim"]: c["status"] for c in payload["claims"]} == {"third_party/": "ok"}
