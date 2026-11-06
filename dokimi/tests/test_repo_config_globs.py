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
        path.write_text(text, encoding="utf-8", newline="")  # LF on every OS
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


def test_a_regular_file_followed_by_a_double_star_is_not_a_match(tmp_path):
    # ** matches directories only, so the zero-directory branch does not accept the file itself
    from dokimi.repo_glob import glob_paths

    (tmp_path / "evaluation").mkdir()
    (tmp_path / "evaluation" / "q.json").write_text("[]\n", encoding="utf-8")
    assert glob_paths(tmp_path, "evaluation/q.json/**") == []
    assert glob_paths(tmp_path, "evaluation/**") == [tmp_path / "evaluation"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX special files")
def test_a_fifo_followed_by_a_double_star_is_not_a_match(tmp_path):
    from dokimi.repo_glob import glob_paths

    (tmp_path / "evaluation").mkdir()
    os.mkfifo(tmp_path / "evaluation" / "q")
    assert glob_paths(tmp_path, "evaluation/q/**") == []


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_an_unreadable_directory_inside_a_skill_is_a_config_error_not_left_out_of_skill_sha(tmp_path):
    # Path.rglob suppresses permission errors, so edits behind the directory would not move skill_sha
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
        "skills/a/scripts/run.py": "print(1)\n",
    })
    scripts = root / "skills" / "a" / "scripts"
    scripts.chmod(0)
    try:
        with pytest.raises(ConfigError, match="scripts"):
            load_repo_config(root).subjects()
    finally:
        scripts.chmod(0o755)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="permissions are not enforced on Windows or for root")
def test_an_unreadable_link_beside_a_literal_suite_file_is_not_read(tmp_path):
    # a literal suite path reads only its own names: an unrelated link to a locked directory is never stat'ed
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/task.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "evaluation/task.json": "[]\n",
        "locked/dir/q.json": "{}\n",
    })
    os.symlink("../locked/dir", root / "evaluation" / "unrelated")
    locked = root / "locked"
    locked.chmod(0)
    try:
        assert "skill/a" in {s.id for s in load_repo_config(root).subjects()}
    finally:
        locked.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_link_to_a_regular_file_beside_a_double_star_suite_is_not_a_suite_link(tmp_path):
    # ** reads through directory links only: a link to a regular file is never read, so it is no link of the suite
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/**/task.json\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "evaluation/task.json": "[]\n",
        "README.md": "readme\n",
    })
    os.symlink("../README.md", root / "evaluation" / "readme.md")
    assert "skill/a" in {s.id for s in load_repo_config(root).subjects()}


def test_renaming_a_suite_file_with_the_same_bytes_moves_suite_hash(tmp_path):
    # the digest of a suite holds each file's name: the same bytes under another name are another suite
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: '{skill}/evals/**'\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "skills/a/evals/q.json": "[]\n",
    })
    before = load_repo_config(root).subjects()[0]
    (root / "skills/a/evals/q.json").rename(root / "skills/a/evals/r.json")
    after = load_repo_config(root).subjects()[0]
    assert after.suite_hash != before.suite_hash
    assert after.skill_sha == before.skill_sha


def test_a_suite_glob_keeps_a_sibling_whose_name_only_starts_with_a_match(tmp_path):
    # eval* matches evals and evals-old; evals-old is not under evals, so its files are the suite's and hash with it
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: '{skill}/eval*'\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "skills/a/evals/q.json": "[]\n",
        "skills/a/evals-old/q.json": "[]\n",
    })
    before = load_repo_config(root).subjects()[0]
    (root / "skills/a/evals-old/q.json").write_text("[2]\n", encoding="utf-8")
    after = load_repo_config(root).subjects()[0]
    assert after.suite_hash != before.suite_hash
    assert after.skill_sha == before.skill_sha


def test_a_terminal_double_star_does_not_claim_a_file_its_prefix_reaches(tmp_path):
    # '*/**' reaches skills/a/SKILL.md through '*', but a terminal ** matches directories only: SKILL.md stays the skill's
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: '{skill}/*/**'\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "skills/a/evals/q.json": "[]\n",
    })
    before = load_repo_config(root).subjects()[0]  # a ConfigError here: the suite claimed SKILL.md, a file of its skill
    (root / "skills/a/SKILL.md").write_text("b\n", encoding="utf-8")
    after = load_repo_config(root).subjects()[0]
    assert after.skill_sha != before.skill_sha and after.suite_hash == before.suite_hash


@pytest.mark.timeout(10)
def test_many_double_stars_in_shared_match_a_deep_change_in_linear_time(tmp_path):
    body = HEADER + "shared: ['" + "/".join(["**"] * 12 + ["zzz"]) + "']\n"
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body})
    deep = "a/" * 24
    impact = load_repo_config(root).subjects_for_changed([deep + "zzz", deep + "b"])
    assert (impact.shared, impact.unclaimed) == ([deep + "zzz"], [deep + "b"])


def test_a_leading_double_star_in_shared_matches_no_directory_so_it_claims_a_root_file(tmp_path):
    # '**/Makefile' claims the root Makefile too: a leading ** matches zero directories, as it does deeper down
    body = HEADER + "shared: ['**/Makefile']\n"
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body, "Makefile": "all:\n", "src/Makefile": "all:\n"})
    impact = load_repo_config(root).subjects_for_changed(["Makefile", "src/Makefile", "src/Makefile.in"])
    assert (impact.shared, impact.unclaimed) == (["Makefile", "src/Makefile"], ["src/Makefile.in"])


@pytest.mark.timeout(10)
def test_many_double_stars_in_a_suite_walk_a_deep_tree_in_linear_time(tmp_path):
    def suite_hash(suite):
        body = HEADER + f"subjects:\n  - id: skill/a\n    path: skills/a\n    suite: '{suite}'\n"
        root = _tree(tmp_path / str(len(suite)), {".github/dokimi/config.yaml": body, "skills/a/SKILL.md": "a\n",
                                                  "evaluation/" + "d/" * 20 + "q.json": "{}\n"})
        return load_repo_config(root).subjects()[0].suite_hash
    assert suite_hash("/".join(["evaluation"] + ["**"] * 10)) == suite_hash("evaluation/**")


def test_a_changed_skill_file_under_a_star_double_star_suite_is_the_skills_in_impact(tmp_path):
    # '{skill}/*/**' does not claim SKILL.md (a terminal ** needs a directory), so its hash moves skill_sha and impact
    # must name the skill, not the suite
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: '{skill}/*/**'\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "skills/a/evals/q.json": "[]\n",
    })
    before = load_repo_config(root).subjects()[0]
    (root / "skills/a/SKILL.md").write_text("b\n", encoding="utf-8")
    config = load_repo_config(root)
    assert config.subjects()[0].skill_sha != before.skill_sha
    impact = config.subjects_for_changed(["skills/a/SKILL.md"])
    assert (dict(impact.skills), dict(impact.suites), impact.unclaimed) == ({"skill/a": ["skills/a/SKILL.md"]}, {}, [])


def test_a_deleted_skill_file_under_a_star_double_star_suite_is_named_for_both(tmp_path):
    # a deleted path may have been a file (skill_sha) or a directory (the suite): impact names both, never neither
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: '{skill}/*/**'\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "a\n",
        "skills/a/evals/q.json": "[]\n",
    })
    before = load_repo_config(root).subjects()[0]
    (root / "skills/a/SKILL.md").unlink()
    config = load_repo_config(root)
    assert config.subjects()[0].skill_sha != before.skill_sha
    impact = config.subjects_for_changed(["skills/a/SKILL.md"])
    assert list(impact.skills) == ["skill/a"] and list(impact.suites) == ["skill/a"]


