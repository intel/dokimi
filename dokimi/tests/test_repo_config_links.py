# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""dokimi_config.v1: links on the way to the config, literal suite paths, nested skills and link chains."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from dokimi import repo_config, repo_links
from dokimi.repo_config import ConfigError, _walk, load_repo_config

HEADER = "schema_version: dokimi_config.v1\n"


def _tree(root: Path, files: dict[str, str]) -> Path:
    """A repository at ``root`` (it always has a ``.git``) holding ``files``; returns the resolved root."""
    if not (root / ".git").exists():  # a real git repository: a .git git cannot read is a config error
        subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")})
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


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_skill_linked_to_the_repository_root_is_a_config_error(tmp_path):
    # its hash would cover the whole repository, and no change to a file could be traced back to the skill
    body = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n"
    root = _tree(tmp_path / "repo", {".github/dokimi/config.yaml": body, "README.md": "x\n"})
    os.makedirs(root / "skills")
    os.symlink("..", root / "skills" / "a")
    with pytest.raises(ConfigError, match="repository root"):
        load_repo_config(root).subjects()


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_link_target_with_the_windows_extended_prefix_is_matched_as_the_plain_path(tmp_path, monkeypatch):
    # os.readlink returns \\?\ paths for absolute links on Windows; the hop must still be found inside the repository
    from dokimi import repo_config

    root = _tree(tmp_path / "repo", {"shared/v1/q.json": "[]\n"})
    os.symlink(root / "shared" / "v1", root / "mid")
    os.symlink(root / "mid", root / "current")  # its target is absolute, and a link on the way to a file in the repo
    real_readlink = os.readlink
    monkeypatch.setattr(
        os, "readlink",
        lambda p: "\\\\?\\" + real_readlink(p) if os.fspath(p).endswith("current") else real_readlink(p),
    )
    assert repo_config._hops(root / "current" / "q.json", root, root) == [("current",), ("mid",)]


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
@pytest.mark.parametrize("suite", ["evaluation/*", "evaluation/loop"])
def test_a_self_referential_link_a_suite_names_is_a_config_error_not_dropped(tmp_path, suite):
    # entry.is_dir() raises ELOOP for evaluation/loop -> loop; the entry must still be listed, so the suite
    # validation refuses it rather than accepting an empty suite
    body = HEADER + f"subjects:\n  - id: skill/a\n    path: skills/a\n    suite: {suite}\n"
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": body,
        "skills/a/SKILL.md": "# a\n",
    })
    os.makedirs(root / "evaluation")
    os.symlink("loop", root / "evaluation" / "loop")
    with pytest.raises(ConfigError, match="loop"):
        load_repo_config(root).subjects()


SKILL_A = HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: {suite}\n"


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks needs privileges on Windows")
def test_a_suite_reached_through_a_link_into_its_own_skill_is_refused(tmp_path):
    # skills/a/evals -> actual, and the suite is skills/a/evals/tasks: the tasks are the skill's own files
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": SKILL_A.format(suite="skills/a/evals/tasks"),
        "skills/a/SKILL.md": "a\n",
        "skills/a/actual/tasks/q.json": "[]\n",
    })
    os.symlink("actual", root / "skills" / "a" / "evals")
    with pytest.raises(ConfigError, match=r"skill/a.*skills/a/evals/tasks.*skills/a/evals"):
        load_repo_config(root).subjects(hashes=False)


def test_a_case_that_differs_only_from_the_name_on_disk_is_the_declared_suite_place(tmp_path, monkeypatch):
    # a case-folding filesystem (Windows) resolves skills/a/evals to skills/a/EVALS; the suite is still the declared place
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n",
        "skills/a/SKILL.md": "a\n",
        "skills/a/EVALS/q.json": "[]\n",
    })
    expected = load_repo_config(root).subjects_for_changed(["skills/a/EVALS/q.json"])
    expected_hashes = {s.id: s.suite_hash for s in load_repo_config(root).subjects()}
    real = repo_links._real

    def folded(path):  # what Windows returns for the declared name: the spelling on disk
        return real(path.parent / "EVALS") if path.name == "evals" else real(path)

    monkeypatch.setattr(repo_links, "_real", folded)
    monkeypatch.setattr(repo_config, "_real", folded)
    assert load_repo_config(root).subjects_for_changed(["skills/a/EVALS/q.json"]) == expected
    assert {s.id: s.suite_hash for s in load_repo_config(root).subjects()} == expected_hashes
    assert expected.skills == {"skill/a": ["skills/a/EVALS/q.json"]} and expected.suites == {}


def test_an_explicit_skill_path_must_be_spelled_as_on_disk(tmp_path, monkeypatch):
    # a case-folding filesystem (Windows) takes skills/A for skills/a and resolves it to the name on disk; the suite globs
    # match names case-sensitively, so the declared spelling would hash the evals as skill content and claim none
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": HEADER + "subjects:\n  - id: skill/a\n    path: skills/A\n",
        "skills/a/SKILL.md": "a\n",
        "skills/a/evals/q.json": "[]\n",
    })
    real, real_is_dir = repo_links._real, Path.is_dir

    def folded(path):  # Windows: the declared name is the directory, and it resolves to the spelling on disk
        return real(path.parent / "a") if path.name == "A" else real(path)

    monkeypatch.setattr(repo_links, "_real", folded)
    monkeypatch.setattr(repo_config, "_real", folded)
    monkeypatch.setattr(Path, "is_dir", lambda self: True if self.name == "A" else real_is_dir(self))
    with pytest.raises(ConfigError, match="skills/a"):
        load_repo_config(root).subjects()


@pytest.mark.skipif(shutil.which("git") is None, reason="the executable bits come from git's index; needs git")
@pytest.mark.parametrize("added", [True, False], ids=["tracked", "untracked"])
def test_a_suite_of_many_files_reads_the_git_index_once(tmp_path, monkeypatch, added):
    # each eval file used to start its own git ls-files: one per file, for every subject's suite hash
    from dokimi import repo_config, utils

    calls: list[Path] = []
    read = utils._tracked_modes

    def counting(base, spec):
        calls.append(base)
        return read(base, spec)

    monkeypatch.setattr(utils, "_tracked_modes", counting)
    monkeypatch.setattr(repo_config, "_tracked_modes", counting)
    files = {"skills/a/SKILL.md": "a\n", **{f"evaluation/t{i}/q.json": "[]\n" for i in range(30)}}
    root = _tree(tmp_path / "repo", {
        ".github/dokimi/config.yaml": HEADER + "subjects:\n  - id: skill/a\n    path: skills/a\n    suite: evaluation/*/q.json\n",
        **files,
    })
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    if added:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subjects = load_repo_config(root).subjects()
    assert subjects[0].suite_hash and len(calls) <= 2  # the skill's own tree and the index, once each
