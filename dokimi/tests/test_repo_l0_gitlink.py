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


def _tree_with_lone_gitlink(root, agents: str) -> GitTree:
    files = {"AGENTS.md": agents, "ev/tasks/t/task.json": "{}", ".gitmodules": _GITMODULES}
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    git = ["git", "-C", str(root)]
    for args in (["init", "-q"], ["add", "-A"],
                 ["update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},third_party/mod"],
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
