# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""#1168: a digest, file list or check of a user's tree raises the OSError of a directory it cannot list, where
``rglob`` and an ``os.walk`` with no ``onerror`` left the directory out and described a smaller tree."""

from __future__ import annotations

import argparse
import contextlib
import errno
import hashlib
import os
from pathlib import Path
from typing import Iterator

import pytest

from dokimi.commands.catalog import cmd_catalog_formats
from dokimi.commands.leak import _refuse_clobber
from dokimi.eval.leakgate import read_skill
from dokimi.harnesses.harbor_export import EXPORT_VERSION, _content_key
from dokimi.harnesses.skill_treatment import check_skill
from dokimi.repos.arms import _uncopyable
from dokimi.repos.conformance import scenarios_of
from dokimi.skills.formats import detect_skill_packages
from dokimi.skills.loader import _not_listed_as_resource, load_skill
from dokimi.treatments.factory import create_treatment
from dokimi.utils import InputError

FRONTMATTER = "---\nname: demo\ndescription: A demo skill.\n---\nBody.\n"


@contextlib.contextmanager
def unlistable(directory: Path) -> Iterator[Path]:
    """``directory`` with mode 111 (searchable, not listable); skips when the process can still list it."""
    directory.chmod(0o111)
    try:
        if os.access(directory, os.R_OK):
            pytest.skip("the process lists a mode 111 directory (root, or a file system with no modes)")
        yield directory
    finally:
        directory.chmod(0o755)


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
    return root


def _skill(tmp_path: Path) -> Path:
    return _write(tmp_path / "skill", {"SKILL.md": FRONTMATTER, "references/a.md": "a\n", "references/plugin.json": "{}"})


def test_content_key_raises_for_a_directory_of_the_task_that_cannot_be_listed(tmp_path: Path):
    task = _write(tmp_path / "task", {"task.yaml": "t\n", "environment/Dockerfile": "FROM x\n"})
    with unlistable(task / "environment"), pytest.raises(PermissionError, match="environment"):
        _content_key(task, "resolved")


def test_content_key_of_a_listable_task_hashes_its_files_by_path_and_content(tmp_path: Path):
    task = _write(tmp_path / "task", {"task.yaml": "t\n", "environment/Dockerfile": "FROM x\n"})
    expected = hashlib.sha256(EXPORT_VERSION.encode() + b"\0" + b"resolved" + b"\0")
    for rel, text in (("environment/Dockerfile", "FROM x\n"), ("task.yaml", "t\n")):
        expected.update(rel.encode() + b"\0" + hashlib.sha256(text.encode()).digest())
    assert _content_key(task, "resolved") == expected.hexdigest()[:16]


def test_read_skill_raises_for_a_directory_of_the_skill_that_cannot_be_listed(tmp_path: Path):
    skill = _skill(tmp_path)
    with unlistable(skill / "references"), pytest.raises(PermissionError, match="references"):
        read_skill(skill)


def test_read_skill_lists_a_listable_skill_and_does_not_list_what_it_leaves_out(tmp_path: Path):
    skill = _write(_skill(tmp_path), {".git/HEAD": "x", "evals/q.json": "{}", "references/__pycache__/m.txt": "x"})
    assert sorted(read_skill(skill)) == ["SKILL.md", "references/a.md", "references/plugin.json"]
    with unlistable(skill / ".git"), unlistable(skill / "evals"), unlistable(skill / "references" / "__pycache__"):
        assert sorted(read_skill(skill)) == ["SKILL.md", "references/a.md", "references/plugin.json"]


def test_load_skill_raises_for_a_directory_of_the_skill_that_cannot_be_listed(tmp_path: Path):
    skill = _skill(tmp_path)
    with unlistable(skill / "references"), pytest.raises(PermissionError, match="references"):
        load_skill(skill)


def test_load_skill_resources_of_a_listable_skill_leave_out_pycache(tmp_path: Path):
    skill = _write(_skill(tmp_path), {"references/__pycache__/m.txt": "x"})
    assert [p.relative_to(skill).as_posix() for p in load_skill(skill).resources] == [
        "references/a.md", "references/plugin.json"]
    with unlistable(skill / "references" / "__pycache__"):
        assert len(load_skill(skill).resources) == 2


@pytest.mark.parametrize("name", [".git", ".cache", "venv", "node_modules"])
def test_load_skill_leaves_out_a_dot_named_or_vendored_directory_it_cannot_list(tmp_path: Path, name: str):
    skill = _write(_skill(tmp_path), {f"{name}/x": "x"})
    with unlistable(skill / name):
        assert len(load_skill(skill).resources) == 2


def test_load_skill_lists_a_listable_dot_directory_and_a_plain_file_named_like_a_vendored_one(tmp_path: Path):
    skill = _write(_skill(tmp_path), {".cache/x": "x", "venv": "v"})
    assert [p.relative_to(skill).as_posix() for p in load_skill(skill).resources] == [
        ".cache/x", "references/a.md", "references/plugin.json", "venv"]


def test_check_skill_says_a_skill_it_cannot_list_does_not_load(tmp_path: Path):
    skill = _skill(tmp_path)
    assert check_skill(skill) == "demo"
    with unlistable(skill / "references"), pytest.raises(ValueError, match="cannot load skill.*references"):
        check_skill(skill)


@pytest.mark.parametrize("kind", ["skill", "skill-agent"])
def test_a_skill_arm_with_a_folder_that_cannot_be_listed_is_refused_as_a_value_error(tmp_path: Path, kind: str):
    skill = _skill(tmp_path)
    with unlistable(skill / "references"), pytest.raises(ValueError, match=f"'{kind}:' folder cannot be listed.*references"):
        create_treatment(f"{kind}:{skill}")


def _eio_when_listing(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    real = os.scandir

    def scandir(path):
        if Path(path).name == name:
            raise OSError(errno.EIO, "Input/output error", str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)


@pytest.mark.parametrize("kind", ["skill", "skill-agent"])
def test_a_skill_arm_whose_folder_fails_to_list_with_an_io_error_is_a_value_error(tmp_path: Path, kind: str, monkeypatch: pytest.MonkeyPatch):
    skill = _skill(tmp_path)
    _eio_when_listing(monkeypatch, "references")
    with pytest.raises(ValueError, match=f"'{kind}:' folder cannot be listed.*Input/output error.*references"):
        create_treatment(f"{kind}:{skill}")


def test_a_skill_catalog_arm_whose_folder_fails_to_list_with_an_io_error_is_a_value_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    skill = _skill(tmp_path / "catalog")
    _eio_when_listing(monkeypatch, "references")
    with pytest.raises(ValueError, match="'skill-catalog:' folder cannot be listed.*Input/output error.*references"):
        create_treatment(f"skill-catalog:{skill.parent}")


@pytest.mark.parametrize("kind", ["skill", "skill-agent"])
def test_a_skill_arm_with_no_skill_md_is_still_a_file_not_found_error(tmp_path: Path, kind: str):
    (tmp_path / "empty").mkdir()
    with pytest.raises(FileNotFoundError):
        create_treatment(f"{kind}:{tmp_path / 'empty'}")


def test_not_listed_as_resource_answers_a_bool_for_each_kind_of_entry(tmp_path: Path):
    _write(tmp_path, {"__pycache__/m": "x", "plain/x": "x", ".open/x": "x", ".shut/x": "x", ".file": "f"})
    assert _not_listed_as_resource(tmp_path / "__pycache__") is True
    assert _not_listed_as_resource(tmp_path / "plain") is False
    assert _not_listed_as_resource(tmp_path / ".file") is False
    assert _not_listed_as_resource(tmp_path / ".open") is False
    with unlistable(tmp_path / ".shut"):
        assert _not_listed_as_resource(tmp_path / ".shut") is True


def test_a_skill_catalog_arm_with_a_folder_that_cannot_be_listed_is_refused_as_a_value_error(tmp_path: Path):
    skill = _skill(tmp_path / "catalog")
    with unlistable(skill / "references"), pytest.raises(ValueError, match="'skill-catalog:' folder cannot be listed.*references"):
        create_treatment(f"skill-catalog:{skill.parent}")


def test_detect_skill_packages_raises_for_a_directory_that_cannot_be_listed(tmp_path: Path):
    root = _write(tmp_path / "root", {"SKILL.md": FRONTMATTER, "plugins/p/.claude-plugin/plugin.json": "{}"})
    with unlistable(root / "plugins"), pytest.raises(PermissionError, match="plugins"):
        detect_skill_packages(root)


def test_detect_skill_packages_lists_a_listable_tree_and_not_a_vendored_directory(tmp_path: Path):
    root = _write(tmp_path / "root", {"SKILL.md": FRONTMATTER, "plugins/p/.claude-plugin/plugin.json": "{}",
                                      "node_modules/x/SKILL.md": FRONTMATTER})
    expected = [("agent-skill", "SKILL.md"), ("claude-plugin", "plugins/p/.claude-plugin/plugin.json")]
    assert [(p.format, p.path.relative_to(root).as_posix()) for p in detect_skill_packages(root)] == expected
    with unlistable(root / "node_modules"):
        assert [(p.format, p.path.relative_to(root).as_posix()) for p in detect_skill_packages(root)] == expected


def test_catalog_formats_exits_through_input_error_for_a_directory_it_cannot_list(tmp_path: Path):
    root = _write(tmp_path / "root", {"SKILL.md": FRONTMATTER, "plugins/p/.claude-plugin/plugin.json": "{}"})
    with unlistable(root / "plugins"), pytest.raises(InputError, match="--root: .*plugins"):
        cmd_catalog_formats(argparse.Namespace(root=str(root), json=False))


def test_scenarios_of_raises_for_a_directory_that_cannot_be_listed(tmp_path: Path):
    root = _write(tmp_path / "fixture", {"CLAUDE.md": "x", "sub/AGENTS.md": "y"})
    assert scenarios_of(root) == {"cwd=.", "cwd=sub", "read=sub"}
    with unlistable(root / "sub"), pytest.raises(PermissionError, match="sub"):
        scenarios_of(root)


def test_uncopyable_raises_for_a_directory_that_cannot_be_listed(tmp_path: Path):
    skill = _skill(tmp_path)
    try:
        (skill / "references" / "out").symlink_to(tmp_path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    assert _uncopyable(skill) == (skill / "references" / "out", "links outside it")
    with unlistable(skill / "references"), pytest.raises(PermissionError, match="references"):
        _uncopyable(skill)


def _clobber_args(skill: Path, out: Path | None) -> argparse.Namespace:
    return argparse.Namespace(skill=str(skill), questions=None, split=None, proposer=None, allow_file=None,
                              out=str(out) if out else None, lock=None)


def test_leak_output_guard_does_not_list_the_skill_when_no_file_is_written(tmp_path: Path):
    skill = _write(_skill(tmp_path), {"evals/q.json": "{}"})
    with unlistable(skill / "evals"):
        _refuse_clobber(_clobber_args(skill, None), None)
        assert len(read_skill(skill)) == 3
        with pytest.raises(InputError, match="evals"):
            _refuse_clobber(_clobber_args(skill, tmp_path / "leak.json"), None)


def test_leak_output_guard_refuses_when_it_cannot_list_the_skill(tmp_path: Path):
    skill = _skill(tmp_path)
    elsewhere = tmp_path / "leak.json"
    _refuse_clobber(_clobber_args(skill, elsewhere), None)
    with pytest.raises(InputError, match="is an input of this run"):
        _refuse_clobber(_clobber_args(skill, skill / "references" / "a.md"), None)
    with unlistable(skill / "references"), pytest.raises(InputError, match="references"):
        _refuse_clobber(_clobber_args(skill, elsewhere), None)


def test_leak_output_guard_accepts_an_existing_output_beside_a_skill_folder(tmp_path: Path):  # test-quality: ok must-not-raise: the guard returns nothing when it accepts
    skill = _skill(tmp_path)
    other = _write(tmp_path / "other", {"leak.json": "{}"})
    _refuse_clobber(_clobber_args(skill, other / "leak.json"), None)
