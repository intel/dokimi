# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi_config.v1: glob walks of suites (unreadable directories) and names with backslashes."""

from __future__ import annotations

import os

import pytest

from dokimi.repo_config import ConfigError, load_repo_config

HEADER = "schema_version: dokimi_config.v1\n"


def _tree(root, files):
    """A repository at ``root`` (it always has a ``.git``) holding ``files``; returns the resolved root."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root.resolve()


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="permissions are not enforced on Windows or for root")
def test_an_unreadable_directory_on_the_way_of_a_suite_glob_is_a_config_error(tmp_path):
    # unlike a skills glob, a suite that cannot see its files must not hash as if it had none
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/locked/tasks/*.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "evaluation/locked/tasks/q.json": "{}\n",
    })
    locked = root / "evaluation" / "locked"
    locked.chmod(0)
    try:
        with pytest.raises(ConfigError, match=r"cannot read .*locked"):
            load_repo_config(root).subjects()
    finally:
        locked.chmod(0o755)


def test_a_backslash_in_a_posix_file_name_is_part_of_the_name(tmp_path):
    # git reports paths with "/" on every OS; on POSIX a backslash is an ordinary character of a file name
    if os.name == "nt":
        pytest.skip("on Windows a backslash separates path parts")
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n"
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body, "skills/a/SKILL.md": "a\n"})
    impact = load_repo_config(root).subjects_for_changed(["skills/a/evals\\q.json"])
    assert impact.unclaimed == []
    assert impact.skills == {"skill/a": ["skills/a/evals\\q.json"]}
