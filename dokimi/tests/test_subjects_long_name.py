# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A subject descriptor path past the file-name limit is a descriptor that does not exist (#1022)."""

from __future__ import annotations

import argparse
import errno
from pathlib import Path

import pytest

from dokimi.commands.subjects import cmd_subjects_list, cmd_subjects_show
from dokimi.subjects import load_subject
from dokimi.utils import InputError


def test_load_subject_reads_a_name_past_the_limit_as_a_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_subject(tmp_path / ("x" * 5000 + ".json"))


def test_a_component_past_name_max_but_inside_path_max_is_refused_the_same_way(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_subject(tmp_path / ("y" * 300) / "subject.toml")


def test_subjects_show_prints_cannot_load_subject_for_a_long_name(tmp_path: Path):
    args = argparse.Namespace(subject=str(tmp_path / ("x" * 5000 + ".json")))
    with pytest.raises(InputError) as raised:
        cmd_subjects_show(args)
    assert str(raised.value).startswith("cannot load subject:")


def test_subjects_list_refuses_a_root_past_the_limit_as_not_a_directory(tmp_path: Path):
    with pytest.raises(InputError) as raised:
        cmd_subjects_list(argparse.Namespace(subjects_root=str(tmp_path / ("x" * 5000))))
    assert str(raised.value).startswith("--subjects-root is not a directory:")


def test_subjects_list_lets_a_permission_error_on_the_root_propagate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def deny(self: Path) -> bool:
        raise PermissionError(errno.EACCES, "Permission denied", str(self))

    monkeypatch.setattr(Path, "is_dir", deny)
    with pytest.raises(PermissionError):
        cmd_subjects_list(argparse.Namespace(subjects_root=str(tmp_path)))


def test_a_name_over_the_limit_with_an_unsupported_suffix_is_still_a_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_subject(tmp_path / ("x" * 5000 + ".yaml"))


def test_a_permission_error_from_the_probe_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def deny(self: Path) -> bool:
        raise PermissionError(errno.EACCES, "Permission denied", str(self))

    monkeypatch.setattr(Path, "is_file", deny)
    with pytest.raises(PermissionError):
        load_subject(tmp_path / "subject.json")


@pytest.mark.parametrize("winerror", [123, 206, 1921])
def test_a_windows_name_error_is_a_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, winerror: int):
    def refuse(self: Path) -> bool:
        error = OSError(errno.EINVAL, "invalid name", str(self))
        error.winerror = winerror  # type: ignore[attr-defined]
        raise error

    monkeypatch.setattr(Path, "is_file", refuse)
    with pytest.raises(FileNotFoundError):
        load_subject(tmp_path / "subject.json")
