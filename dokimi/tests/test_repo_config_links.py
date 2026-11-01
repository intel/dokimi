# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi_config.v1: links on the way to the config, literal suite paths, nested skills and link chains."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dokimi.repo_config import ConfigError, _walk, load_repo_config

HEADER = "schema_version: dokimi_config.v1\n"


def _tree(root: Path, files: dict[str, str]) -> Path:
    """A repository at ``root`` (it always has a ``.git``) holding ``files``; returns the resolved root."""
    (root / ".git").mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root.resolve()


def _subjects(root: Path):
    return {s.id: s for s in load_repo_config(root).subjects()}


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_retargeting_a_link_on_the_way_to_the_config_moves_the_smoke_set(tmp_path):
    root = _tree(tmp_path / "repo", {
        "one/config.yaml": HEADER + "subjects: []\n",
        "two/config.yaml": HEADER + "subjects: []\n",
    })
    os.makedirs(root / ".github" / "dokimi")
    os.makedirs(root / "ci")
    os.symlink("../one", root / "ci" / "current")
    os.symlink("../../ci/current/config.yaml", root / ".github" / "dokimi" / "config.yaml")
    (root / "ci" / "current").unlink()
    os.symlink("../two", root / "ci" / "current")
    impact = load_repo_config(root).subjects_for_changed(["ci/current"])
    assert impact.shared == ["ci/current"]
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_retargeting_a_link_to_a_suite_file_that_is_missing_there_moves_the_suite(tmp_path):
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/current/task.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "one/task.json": "[]\n",
        "two/other.json": "[]\n",  # no task.json here: the suite is empty after the retarget
    })
    os.makedirs(root / "evaluation")
    os.symlink("../one", root / "evaluation" / "current")
    before = _subjects(root)["skill/a"].suite_hash
    (root / "evaluation" / "current").unlink()
    os.symlink("../two", root / "evaluation" / "current")
    assert _subjects(root)["skill/a"].suite_hash != before
    impact = load_repo_config(root).subjects_for_changed(["evaluation/current"])
    assert impact.suites == {"skill/a": ["evaluation/current"]}
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_retargeting_a_link_to_a_wildcard_suite_that_matches_nothing_there_moves_the_suite(tmp_path):
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/current/*.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "one/task.json": "[]\n",
        "two/other.md": "x\n",  # no JSON here: the suite is empty after the retarget
    })
    os.makedirs(root / "evaluation")
    os.symlink("../one", root / "evaluation" / "current")
    before = _subjects(root)["skill/a"].suite_hash
    (root / "evaluation" / "current").unlink()
    os.symlink("../two", root / "evaluation" / "current")
    assert _subjects(root)["skill/a"].suite_hash != before
    impact = load_repo_config(root).subjects_for_changed(["evaluation/current"])
    assert impact.suites == {"skill/a": ["evaluation/current"]}
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_deleting_the_only_file_a_wildcard_suite_matches_still_claims_it(tmp_path):
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/current/*.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "one/task.json": "[]\n",
    })
    os.makedirs(root / "evaluation")
    os.symlink("../one", root / "evaluation" / "current")
    before = _subjects(root)["skill/a"].suite_hash
    (root / "one" / "task.json").unlink()
    assert _subjects(root)["skill/a"].suite_hash != before
    impact = load_repo_config(root).subjects_for_changed(["one/task.json"])
    assert impact.suites == {"skill/a": ["one/task.json"]}
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_dotdot_after_a_link_in_an_absolute_target_keeps_that_link_in_reach(tmp_path):
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/q.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "one/q.json": "[]\n",
        "one/sub/keep": "x\n",
        "two/q.json": "[]\n",
        "two/sub/keep": "x\n",
    })
    os.makedirs(root / "evaluation")
    os.makedirs(root / "shared")
    os.symlink("../one/sub", root / "shared" / "current")
    os.symlink(root / "shared" / "current" / ".." / "q.json", root / "evaluation" / "q.json")
    before = _subjects(root)["skill/a"].suite_hash
    (root / "shared" / "current").unlink()
    os.symlink("../two/sub", root / "shared" / "current")
    assert _subjects(root)["skill/a"].suite_hash != before
    impact = load_repo_config(root).subjects_for_changed(["shared/current"])
    assert impact.suites == {"skill/a": ["shared/current"]}
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_deleting_the_file_a_linked_literal_suite_names_still_claims_it(tmp_path):
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/current/task.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "one/task.json": "[]\n",
    })
    os.makedirs(root / "evaluation")
    os.symlink("../one", root / "evaluation" / "current")
    before = _subjects(root)["skill/a"].suite_hash
    (root / "one" / "task.json").unlink()
    assert _subjects(root)["skill/a"].suite_hash != before
    impact = load_repo_config(root).subjects_for_changed(["one/task.json"])
    assert impact.suites == {"skill/a": ["one/task.json"]}
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_nested_skill_linked_to_a_sibling_inside_the_outer_skill_is_refused(tmp_path):
    body = HEADER + ("subjects:\n  - id: skill/outer\n    path: skills/outer\n"
                     "  - id: skill/inner\n    path: skills/outer/inner\n")
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/outer/SKILL.md": "# outer\n",
        "skills/outer/actual/SKILL.md": "# actual\n",
    })
    os.symlink(root / "skills/outer/actual", root / "skills/outer/inner", target_is_directory=True)
    with pytest.raises(ConfigError, match=r"skill/inner.*not supported"):
        load_repo_config(root).subjects(hashes=False)


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_link_inside_a_suite_reached_through_another_link_is_not_a_skill_link(tmp_path):
    # skill_sha leaves the suite's files out, so retargeting a link inside the suite must not claim the skill
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: skills/a/evals/**\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "x/one.md": "same\n",
        "x/two.md": "same\n",
    })
    os.makedirs(root / "skills" / "a" / "evals")
    os.symlink("../../../x/one.md", root / "skills" / "a" / "evals" / "alias")
    os.symlink("alias", root / "skills" / "a" / "evals" / "q.json")
    before = _subjects(root)["skill/a"]
    (root / "skills" / "a" / "evals" / "alias").unlink()
    os.symlink("../../../x/two.md", root / "skills" / "a" / "evals" / "alias")
    after = _subjects(root)["skill/a"]
    assert after.skill_sha == before.skill_sha and after.suite_hash != before.suite_hash
    impact = load_repo_config(root).subjects_for_changed(["skills/a/evals/alias"])
    assert impact.skills == {}
    assert impact.suites == {"skill/a": ["skills/a/evals/alias"]}


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_deleting_one_file_a_wildcard_suite_matches_among_others_still_claims_it(tmp_path):
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/current/*.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "one/task.json": "[]\n",
        "one/keep.json": "[]\n",
    })
    os.makedirs(root / "evaluation")
    os.symlink("../one", root / "evaluation" / "current")
    before = _subjects(root)["skill/a"].suite_hash
    (root / "one" / "task.json").unlink()
    assert _subjects(root)["skill/a"].suite_hash != before
    impact = load_repo_config(root).subjects_for_changed(["one/task.json"])
    assert impact.suites == {"skill/a": ["one/task.json"]}
    assert impact.unclaimed == []


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_link_inside_a_suite_under_a_linked_skill_is_not_a_skill_link(tmp_path):
    # skills/a -> vendor/a: the suite is spelled under skills/a but its files live under vendor/a. The eval link
    # is in the suite, so skill_sha leaves it out, whichever spelling the walk reached it by
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: skills/a/evals/**\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "vendor/a/SKILL.md": "# a\n",
        "x/one.md": "same\n",
        "x/two.md": "same\n",
    })
    os.makedirs(root / "skills")
    os.symlink("../vendor/a", root / "skills" / "a")
    os.makedirs(root / "vendor" / "a" / "evals")
    os.symlink("../../../x/one.md", root / "vendor" / "a" / "evals" / "q.json")
    before = _subjects(root)["skill/a"]
    (root / "vendor" / "a" / "evals" / "q.json").unlink()
    os.symlink("../../../x/two.md", root / "vendor" / "a" / "evals" / "q.json")
    after = _subjects(root)["skill/a"]
    assert after.skill_sha == before.skill_sha and after.suite_hash != before.suite_hash
    impact = load_repo_config(root).subjects_for_changed(["vendor/a/evals/q.json"])
    assert impact.skills == {}
    assert impact.suites == {"skill/a": ["vendor/a/evals/q.json"]}


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_mixed_case_directory_aliases_are_walked_in_code_point_order_on_every_os(tmp_path):
    # Path ordering folds case on Windows, where "a" sorts before "B"; POSIX sorts "B" first. The walk takes the first
    # alias of a shared directory it meets, so the suite must name the same alias on every OS: children by name
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "shared/q.json": "[]\n",
    })
    os.makedirs(root / "evaluation")
    os.symlink("../shared", root / "evaluation" / "B")
    os.symlink("../shared", root / "evaluation" / "a")
    names = [name for name, _ in _walk(root / "evaluation", "evaluation")]
    assert "evaluation/B/q.json" in names
    assert "evaluation/a/q.json" not in names
