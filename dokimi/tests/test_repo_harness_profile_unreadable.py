# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`repo budget` and `repo reach` exit 2 with a message on a --harness file that cannot be read."""

from __future__ import annotations

import os

import pytest

from dokimi.cli import build_parser
from dokimi.repos.harness_profile import ProfileError, load_profile


def _run(*argv: str) -> None:
    args = build_parser().parse_args(["repo", *argv])
    args.func(args)


def _bad_profile(tmp_path, kind: str):
    path = tmp_path / "profile.yaml"
    path.write_bytes(b"name: \xff\xfe\n")
    if kind == "unreadable":
        if os.name == "nt" or os.geteuid() == 0:
            pytest.skip("file permissions do not stop this user from reading")
        path.write_text("name: x\n", encoding="utf-8")
        path.chmod(0)
    return path


@pytest.mark.parametrize("kind", ["non-utf8", "unreadable"])
def test_load_profile_names_the_file_it_cannot_read(tmp_path, kind):
    path = _bad_profile(tmp_path, kind)
    with pytest.raises(ProfileError, match="profile.yaml"):
        load_profile(path)


def test_load_profile_names_a_file_whose_read_raises_oserror(tmp_path, monkeypatch):
    path = tmp_path / "profile.yaml"
    path.write_text("name: x", encoding="utf-8")

    def deny(self, *args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.read_text", deny)
    with pytest.raises(ProfileError, match="profile.yaml.*denied"):
        load_profile(path)


@pytest.mark.parametrize("kind", ["non-utf8", "unreadable"])
def test_repo_budget_exits_2_on_a_harness_file_it_cannot_read(tmp_path, capsys, kind):
    path = _bad_profile(tmp_path, kind)
    with pytest.raises(SystemExit) as exc:
        _run("budget", str(tmp_path), "--harness", str(path))
    assert exc.value.code == 2
    assert str(path) in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["non-utf8", "unreadable"])
def test_repo_reach_exits_2_on_a_harness_file_it_cannot_read(tmp_path, capsys, kind):
    path = _bad_profile(tmp_path, kind)
    with pytest.raises(SystemExit) as exc:
        _run("reach", str(tmp_path), "--model", "m", "--harness", str(path), "--dry-run")
    assert exc.value.code == 2
    assert str(path) in capsys.readouterr().err
