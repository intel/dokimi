# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget` on a git working tree with a tracked file under a directory that cannot be searched (#959)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from dokimi.cli import build_parser
from dokimi.repos.budget import budget
from dokimi.repos.source import WorkTree

NAMES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")

pytestmark = pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                                reason="root ignores directory modes")


def _repo(path):
    git = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)  # noqa: E731
    (path / "d").mkdir()
    (path / "d" / "f.txt").write_text("hi\n", encoding="utf-8")
    (path / "AGENTS.md").write_text("x\n", encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "case")


def test_a_tracked_file_under_an_unsearchable_directory_is_skipped_and_named(tmp_path, capsys):
    _repo(tmp_path)
    (tmp_path / "d").chmod(0o000)
    try:
        tree = WorkTree(tmp_path)
        assert tree.files() == ["AGENTS.md"]
        assert {e["path"] for e in budget(tree, NAMES)["entries"]} == {"AGENTS.md"}
    finally:
        (tmp_path / "d").chmod(0o755)
    assert tree.skipped == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]
    assert capsys.readouterr().err == ""


def test_a_searchable_directory_lists_its_files_without_a_warning(tmp_path):
    _repo(tmp_path)
    tree = WorkTree(tmp_path)
    assert tree.files() == ["AGENTS.md", "d/f.txt"]
    assert tree.skipped == []


def test_the_warning_does_not_depend_on_pathlib_raising(tmp_path, capsys, monkeypatch):
    """#959: Python 3.14's `Path.is_file` and `Path.is_symlink` return False on PermissionError instead of
    raising; the path must still be named in `skipped`."""
    def quiet(original):
        def check(self):
            try:
                return original(self)
            except PermissionError:
                return False
        return check

    _repo(tmp_path)
    monkeypatch.setattr(Path, "is_file", quiet(Path.is_file))
    monkeypatch.setattr(Path, "is_symlink", quiet(Path.is_symlink))
    (tmp_path / "d").chmod(0o000)
    try:
        tree = WorkTree(tmp_path)
        assert tree.files() == ["AGENTS.md"]
    finally:
        (tmp_path / "d").chmod(0o755)
    assert tree.skipped == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]


def test_a_link_and_a_deleted_file_in_the_listing(tmp_path):
    _repo(tmp_path)
    (tmp_path / "link").symlink_to("nowhere")
    (tmp_path / "d" / "f.txt").unlink()
    subprocess.run(["git", "-C", str(tmp_path), "add", "link"], check=True, capture_output=True)
    tree = WorkTree(tmp_path)
    assert tree.files() == ["AGENTS.md", "link"]  # a dangling link stays, a deleted file goes
    assert tree.skipped == []


def test_the_skipped_path_is_a_budget_warning_that_fails_the_gate(tmp_path, capsys):
    """#959; schemas/repo_budget.v1.json: `warnings` is what `--fail-on-warning` gates on, unreadable files included."""
    _repo(tmp_path)
    (tmp_path / "d").chmod(0o000)
    try:
        assert budget(WorkTree(tmp_path), NAMES)["warnings"] == ["d/f.txt: Permission denied; the file and anything it leads to are not counted"]
        args = build_parser().parse_args(["repo", "budget", str(tmp_path), "--fail-on-warning"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
    finally:
        (tmp_path / "d").chmod(0o755)
    assert exc.value.code == 1
    assert "warning: d/f.txt: Permission denied; the file and anything it leads to are not counted" in capsys.readouterr().out


def test_a_skipped_guidance_file_makes_repo_scan_incomplete(tmp_path, capsys):
    """#959; `repo scan --fail-on-fail` exits 1 on an incomplete scan: a guidance file it could not list is not checked."""
    _repo(tmp_path)
    (tmp_path / "d" / "AGENTS.md").write_text("Read `missing.py`.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    (tmp_path / "d").chmod(0o000)
    try:
        args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--fail-on-fail", "--json"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
    finally:
        (tmp_path / "d").chmod(0o755)
    assert exc.value.code == 1
    assert "d/AGENTS.md: Permission denied" in capsys.readouterr().err


def test_a_skipped_guidance_file_makes_a_written_manifest_scan_incomplete(tmp_path, capsys):
    """#959; the same gate with `.agent-eval/repo-eval.yaml`, which does not go through the budget warnings."""
    _repo(tmp_path)
    (tmp_path / "d" / "AGENTS.md").write_text("Read `missing.py`.\n", encoding="utf-8")
    (tmp_path / ".agent-eval").mkdir()
    (tmp_path / ".agent-eval" / "repo-eval.yaml").write_text(
        '{"schema": "repo_manifest.v1", "id": "toy", "guidance": {"globs": ["**/AGENTS.md"]}}\n', encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    (tmp_path / "d").chmod(0o000)
    try:
        args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--fail-on-fail", "--json"])
        with pytest.raises(SystemExit) as exc:
            args.func(args)
    finally:
        (tmp_path / "d").chmod(0o755)
    assert exc.value.code == 1
    assert "d/AGENTS.md: Permission denied" in capsys.readouterr().err


@pytest.mark.parametrize("written", [False, True])
def test_a_skipped_data_file_does_not_make_repo_scan_incomplete(tmp_path, written):
    """#959 review of #1028: only a skipped path the scan would check is a reason; `d/f.txt` is not guidance."""
    _repo(tmp_path)
    if written:
        (tmp_path / ".agent-eval").mkdir()
        (tmp_path / ".agent-eval" / "repo-eval.yaml").write_text(
            '{"schema": "repo_manifest.v1", "id": "toy", "guidance": {"globs": ["**/AGENTS.md"]}}\n', encoding="utf-8")
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
    (tmp_path / "d").chmod(0o000)
    try:
        args = build_parser().parse_args(["repo", "scan", str(tmp_path), "--fail-on-fail", "--json"])
        try:
            args.func(args)
            code = 0
        except SystemExit as exc:
            code = exc.code
    finally:
        (tmp_path / "d").chmod(0o755)
    assert code in (0, None)
