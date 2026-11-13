# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The workspace's git config is set aside before grading once, and a crash between the moves does not
lose it (#687): the next grade finishes the moves instead of taking them for a regrade's."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dokimi.repos.grading import AGENT_GIT, _set_git_config_aside


class Crash(BaseException):
    """The process dies: no ``except Exception`` on the way catches it."""


AGENT_CONFIG = "[core]\n\tfsmonitor = ./agent-hook\n"
SOURCES = {"config": AGENT_CONFIG, "config.worktree": "[alias]\n\tx = !agent\n", "commondir": "../agent\n"}


def _agent_git(run_dir: Path) -> Path:
    dot_git = run_dir / "workspace" / ".git"
    (dot_git / "hooks").mkdir(parents=True)
    (dot_git / "hooks" / "pre-commit").write_text("#!/bin/sh\necho agent\n", encoding="utf-8")
    for name, text in SOURCES.items():
        (dot_git / name).write_text(text, encoding="utf-8")
    return dot_git


def _archived(run_dir: Path) -> dict[str, str]:
    aside = run_dir / AGENT_GIT
    out = {p.name: p.read_text(encoding="utf-8") for p in aside.iterdir() if p.is_file()}
    out["hooks/pre-commit"] = (aside / "hooks" / "pre-commit").read_text(encoding="utf-8")
    return out


@pytest.mark.parametrize("moves_before_crash", [0, 1, 3])
def test_a_crash_between_the_moves_loses_nothing_on_the_next_grade(tmp_path, monkeypatch, moves_before_crash):
    dot_git = _agent_git(tmp_path)
    real, done = os.rename, []

    def crash_after_some(src, dst):
        if Path(dst).parent.name == f".{AGENT_GIT}.partial":
            if len(done) == moves_before_crash:
                raise Crash
            done.append(src)
        return real(src, dst)

    monkeypatch.setattr(os, "rename", crash_after_some)
    with pytest.raises(Crash):
        _set_git_config_aside(tmp_path)
    monkeypatch.setattr(os, "rename", real)
    _set_git_config_aside(tmp_path)
    assert _archived(tmp_path) == {**SOURCES, "hooks/pre-commit": "#!/bin/sh\necho agent\n"}
    assert sorted(p.name for p in dot_git.iterdir()) == ["config"]
    assert "fsmonitor" not in (dot_git / "config").read_text(encoding="utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == [AGENT_GIT, "workspace"]  # no partial archive left


def test_a_regrade_keeps_the_first_archive_and_drops_what_the_grader_wrote(tmp_path):
    dot_git = _agent_git(tmp_path)
    _set_git_config_aside(tmp_path)
    (dot_git / "config").write_text("[core]\n\tfsmonitor = ./grader\n", encoding="utf-8")
    (dot_git / "hooks").mkdir()
    _set_git_config_aside(tmp_path)
    assert _archived(tmp_path)["config"] == AGENT_CONFIG
    assert sorted(p.name for p in dot_git.iterdir()) == ["config"]
    assert "fsmonitor" not in (dot_git / "config").read_text(encoding="utf-8")


def test_a_regrade_keeps_the_first_git_file_and_removes_a_later_one(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").write_text("gitdir: /agent/repo\n", encoding="utf-8")
    _set_git_config_aside(tmp_path)
    (workspace / ".git").write_text("gitdir: /grader/repo\n", encoding="utf-8")
    _set_git_config_aside(tmp_path)
    assert (tmp_path / AGENT_GIT / "dot-git").read_text(encoding="utf-8") == "gitdir: /agent/repo\n"
    assert not (workspace / ".git").exists()


def test_a_link_in_place_of_the_partial_archive_is_not_moved_into(tmp_path):
    run_dir, outside = tmp_path / "rd", tmp_path / "outside"
    outside.mkdir()
    _agent_git(run_dir)
    (run_dir / f".{AGENT_GIT}.partial").symlink_to(outside, target_is_directory=True)  # a grader's code left it
    _set_git_config_aside(run_dir)
    assert not list(outside.iterdir())
    assert not (run_dir / AGENT_GIT).is_symlink() and _archived(run_dir)["config"] == AGENT_CONFIG
