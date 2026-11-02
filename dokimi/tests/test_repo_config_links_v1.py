# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi_config.v1 link rule: a skill may be a link to a place inside the repository; any other link on the way to
or inside a skill or suite is a ConfigError, and the deletion of a link is still mapped to the skill and the suite."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from dokimi.repo_config import ConfigError, load_repo_config

HEADER = "schema_version: dokimi_config.v1\n"
SKILL_A = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: {suite}\n"
symlinks = pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")


def _tree(root, files):
    """A repository at ``root`` (it always has a ``.git``) holding ``files``; returns the resolved root."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root.resolve()


def _subjects(root):
    return {s.id: s for s in load_repo_config(root).subjects()}


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@symlinks
@pytest.mark.parametrize("case", ["suite_link", "suite_file_link", "skill_inside_link", "skill_way_link",
                                  "suite_way_link", "skill_outside_link", "repo_suite_link"])
def test_a_link_other_than_a_whole_skill_directory_is_a_config_error(tmp_path, case):
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite="evaluation/current/*.json"),
        "skills/a/SKILL.md": "# a\n",
        "one/task.json": "[]\n",
        "real/a/SKILL.md": "# a\n",
        "shared/evaluation/q.json": "[]\n",
        "shared/x": "x\n",
    })
    if case == "suite_link":  # the suite's directory is a link to another directory
        os.makedirs(root / "evaluation")
        os.symlink("../one", root / "evaluation" / "current")
    elif case == "suite_file_link":  # a suite match that is itself a link to a file
        os.makedirs(root / "evaluation" / "current")
        os.symlink("../../shared/x", root / "evaluation" / "current" / "task.json")
    elif case == "skill_inside_link":  # a link inside the skill
        os.makedirs(root / "skills" / "a" / "refs")
        os.symlink("../../../shared", root / "skills" / "a" / "refs" / "current")
    elif case == "skill_way_link":  # the skill is reached through a link that is not the skill itself
        os.rename(root / "skills", root / "real-skills")
        os.symlink("real-skills", root / "skills")
    elif case == "suite_way_link":  # the suite's file is reached through a link above it
        (root / ".github/dokimi/config.yaml").write_text(
            SKILL_A.format(suite="evaluation/q.json"), encoding="utf-8")
        os.symlink("shared/evaluation", root / "evaluation")
    elif case == "skill_outside_link":  # the skill directory links outside the repository
        outside = root.parent / "outside-a"
        outside.mkdir()
        (outside / "SKILL.md").write_text("# a\n", encoding="utf-8")
        (root / "skills" / "a").rename(root.parent / "moved-a")
        os.symlink(outside, root / "skills" / "a")
    elif case == "repo_suite_link":  # a repository subject whose suite directory is a link
        (root / ".github/dokimi/config.yaml").write_text(
            HEADER + "subjects:\n  - id: repo\n    kind: repo\n    manifest: README.md\n    suite: docs/**\n",
            encoding="utf-8")
        (root / "README.md").write_text("# r\n", encoding="utf-8")
        os.makedirs(root / "real-docs")
        (root / "real-docs" / "d.md").write_text("d\n", encoding="utf-8")
        os.symlink("real-docs", root / "docs")
    with pytest.raises(ConfigError, match="link"):
        load_repo_config(root).subjects()


@symlinks
@pytest.mark.parametrize("suite", ["evaluation/current/task.json", "evaluation/*/task.json", "evaluation/**/task.json"])
@pytest.mark.parametrize("target_present", [True, False])
def test_a_link_on_the_way_to_a_suite_is_refused_whether_or_not_its_file_exists(tmp_path, suite, target_present):
    """The config must not depend on the disk: a link the suite's walk reads through is refused with no match under it,
    whether the link is named literally or met by a wildcard."""
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite=suite),
        "skills/a/SKILL.md": "# a\n",
        "one/keep": "x\n",
    })
    os.makedirs(root / "evaluation")
    os.symlink("../one", root / "evaluation" / "current")
    if target_present:
        (root / "one" / "task.json").write_text("[]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="reached through the link evaluation/current"):
        load_repo_config(root).subjects()


@symlinks
def test_a_skill_directory_that_links_inside_the_repository_is_accepted(tmp_path):
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite="skills/a/evals/**"),
        "real/a/SKILL.md": "# a\n",
        "real/a/evals/q.json": "[]\n",
    })
    os.makedirs(root / "skills")
    os.symlink("../real/a", root / "skills" / "a")
    subject = _subjects(root)["skill/a"]
    assert subject.suite_hash and subject.skill_sha
    impact = load_repo_config(root).subjects_for_changed(["real/a/SKILL.md"])
    assert impact.skills == {"skill/a": ["real/a/SKILL.md"]}
    assert impact.unclaimed == []


@symlinks
@pytest.mark.parametrize("suite", ["evaluation/current/task.json", "evaluation/current/*.json", "evaluation/*/task.json"])
def test_a_link_on_the_way_to_a_suite_that_is_gone_still_claims_the_suite(tmp_path, suite):
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite=suite),
        "skills/a/SKILL.md": "a\n",
    })
    os.makedirs(root / "evaluation")  # the link that was here is deleted: nothing is on disk at the path now
    impact = load_repo_config(root).subjects_for_changed(["evaluation/current"])
    assert impact.suites == {"skill/a": ["evaluation/current"]}
    assert impact.unclaimed == []


@symlinks
def test_a_link_inside_a_skill_that_is_deleted_claims_the_skill_and_the_suite(tmp_path):
    """The link ``skills/a/refs/current`` lived in the skill and was the way to the suite; with it gone, the change
    still moves both: skill_sha hashed the link, and the suite's glob runs through its path."""
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite="skills/a/refs/current/tasks/**"),
        "skills/a/SKILL.md": "# a\n",
        "skills/a/refs/keep": "x\n",
    })
    impact = load_repo_config(root).subjects_for_changed(["skills/a/refs/current"])
    assert impact.skills == {"skill/a": ["skills/a/refs/current"]}
    assert impact.suites == {"skill/a": ["skills/a/refs/current"]}
    assert impact.unclaimed == []


def test_a_real_suite_whose_only_file_is_deleted_still_claims_the_suite(tmp_path):
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite="evaluation/current/*.json"),
        "skills/a/SKILL.md": "# a\n",
        "evaluation/current/task.json": "[]\n",
    })
    before = _subjects(root)["skill/a"].suite_hash
    (root / "evaluation" / "current" / "task.json").unlink()
    assert _subjects(root)["skill/a"].suite_hash != before
    impact = load_repo_config(root).subjects_for_changed(["evaluation/current/task.json"])
    assert impact.suites == {"skill/a": ["evaluation/current/task.json"]}
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="the executable bit is not kept on Windows")
def test_a_file_of_a_nested_checkout_takes_its_mode_from_that_repository(tmp_path):
    """A file of a nested checkout is not in the outer index, so its mode is the nested repository's index bit, not the
    disk bit. The index says executable and the disk does not: chmod on disk must not move the suite hash. The nested
    .git/index bytes are left alone, so only the mode can move the hash."""
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite="evaluation/**"),
        "skills/a/SKILL.md": "# a\n",
    })
    shutil.rmtree(root / ".git")
    _git(root, "init", "-q")  # a real outer repository: its index holds the skill files, not evaluation/
    _git(root, "add", "--", ".github", "skills")
    nested = root / "evaluation" / "nested"
    nested.mkdir(parents=True)
    (nested / "q.sh").write_text("echo\n", encoding="utf-8")
    _git(nested, "init", "-q")
    _git(nested, "add", "q.sh")
    _git(nested, "update-index", "--chmod=+x", "q.sh")
    before = _subjects(root)["skill/a"].suite_hash
    os.chmod(nested / "q.sh", 0o755)
    assert _subjects(root)["skill/a"].suite_hash == before
