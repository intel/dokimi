# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The pre-flight write checks try the write (#531): ``os.access`` on Windows reads the read-only attribute of a
file, is true for any directory and does not see an ACL that denies the write. The refusals are patched in, so
these tests run on Windows and as root alike."""

from __future__ import annotations

import os
import tempfile

import pytest

from dokimi.utils import InputError
from dokimi.write_probe import can_write


def _access_says_yes(monkeypatch):
    monkeypatch.setattr(os, "access", lambda *args, **kwargs: True)


def _refuse_open(monkeypatch):
    def refuse(path, mode="r", *args, **kwargs):
        raise PermissionError(13, "Access is denied", os.fspath(path))

    monkeypatch.setattr("dokimi.write_probe.open", refuse, raising=False)


def _refuse_new_file(monkeypatch, where):
    real = tempfile.mkstemp

    def mkstemp(*args, dir=None, **kwargs):
        if dir is not None and os.path.realpath(dir) == os.path.realpath(where):
            raise PermissionError(13, "Access is denied", os.fspath(dir))
        return real(*args, dir=dir, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)


def test_a_file_is_probed_without_truncating_it(tmp_path):
    target = tmp_path / "routing.json"
    target.write_text("kept", encoding="utf-8")
    assert can_write(target) is True
    assert target.read_text(encoding="utf-8") == "kept"


def test_a_directory_is_probed_without_leaving_a_file(tmp_path):
    assert can_write(tmp_path) is True
    assert list(tmp_path.iterdir()) == []


def test_a_directory_that_refuses_only_the_delete_is_writable(tmp_path, monkeypatch):
    """Windows: an ACL that allows creating and writing a file and denies deleting it. The routing writer needs no
    delete access, so the probe must not (``TemporaryFile`` is delete-on-close there, and ``unlink`` is refused)."""
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(tempfile, "TemporaryFile", refuse)
    monkeypatch.setattr(os, "unlink", refuse)
    assert can_write(tmp_path) is True


def test_a_file_that_refuses_the_write_is_not_writable(tmp_path, monkeypatch):
    target = tmp_path / "routing.json"
    target.write_text("kept", encoding="utf-8")
    _refuse_open(monkeypatch)
    assert can_write(target) is False


def test_a_directory_that_refuses_the_write_is_not_writable(tmp_path, monkeypatch):
    _refuse_new_file(monkeypatch, tmp_path)
    assert can_write(tmp_path) is False


def test_routing_out_file_that_refuses_the_write_is_refused_whatever_access_says(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    out = tmp_path / "routing.json"
    out.write_text("{}", encoding="utf-8")
    _access_says_yes(monkeypatch)
    _refuse_open(monkeypatch)
    with pytest.raises(InputError, match="not writable"):
        checked_out_path(str(out), "routing")
    assert out.read_text(encoding="utf-8") == "{}"


def test_routing_out_in_a_directory_that_refuses_the_write_is_refused_whatever_access_says(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    _access_says_yes(monkeypatch)
    _refuse_new_file(monkeypatch, tmp_path)
    with pytest.raises(InputError, match="not writable"):
        checked_out_path(str(tmp_path / "routing.json"), "routing")
    assert list(tmp_path.iterdir()) == []


def test_routing_out_that_takes_the_write_is_accepted_and_nothing_is_left(tmp_path):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    out = tmp_path / "new" / "routing.json"
    assert checked_out_path(str(out), "routing") == out
    assert list(tmp_path.iterdir()) == []


def test_cells_directory_that_refuses_the_write_exits_2(tmp_path, monkeypatch, capsys):
    from dokimi.commands.arms import _check_cells_dir

    _access_says_yes(monkeypatch)
    _refuse_new_file(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exit_:
        _check_cells_dir(tmp_path / "cells")
    assert exit_.value.code == 2
    assert capsys.readouterr().err == (
        f"Error: cannot write {tmp_path / 'cells'}: {tmp_path} is not a writable directory\n")


def test_cells_directory_that_takes_the_write_passes_and_nothing_is_made(tmp_path):
    from dokimi.commands.arms import _check_cells_dir

    _check_cells_dir(tmp_path / "cells")
    assert list(tmp_path.iterdir()) == []


def test_trigger_probe_out_in_a_directory_that_refuses_the_write_is_refused_before_any_run(tmp_path, monkeypatch):
    from test_trigger_probe import _cli

    _access_says_yes(monkeypatch)
    _refuse_new_file(monkeypatch, tmp_path)
    with pytest.raises(InputError, match="--out: not a writable file path"):
        _cli(tmp_path, monkeypatch, "--out", "probe.json")
    assert not (tmp_path / "probe.json").exists()
