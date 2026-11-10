# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The pre-flight write checks try the write (#531): ``os.access`` on Windows reads the read-only attribute of a
file, is true for any directory and does not see an ACL that denies the write. The refusals are patched in, so
these tests run on Windows and as root alike."""

from __future__ import annotations

import errno
import os

import pytest

from dokimi.utils import InputError
from dokimi.write_probe import can_write


def _access_says_yes(monkeypatch):
    monkeypatch.setattr(os, "access", lambda *args, **kwargs: True)


def _refuse_open(monkeypatch):
    def refuse(path, mode="r", *args, **kwargs):
        raise PermissionError(13, "Access is denied", os.fspath(path))

    monkeypatch.setattr("dokimi.write_probe.open", refuse, raising=False)


def _refuse_new_file(monkeypatch, where, *, only_reading=False):
    """Creating a file in ``where`` is refused; with ``only_reading``, only when it is opened with read access (a
    directory whose new files inherit an ACL that denies FILE_READ_DATA)."""
    real = os.open

    def refuse(path, flags, *args, **kwargs):
        reads = flags & (os.O_RDWR | os.O_WRONLY) != os.O_WRONLY
        if os.path.realpath(os.path.dirname(path)) == os.path.realpath(where) and (reads or not only_reading):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", refuse)


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

    monkeypatch.setattr(os, "unlink", refuse)
    assert can_write(tmp_path) is True


def test_a_directory_that_refuses_the_delete_of_a_made_directory_is_writable(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(os, "unlink", refuse)
    monkeypatch.setattr(os, "rmdir", refuse)
    assert can_write(tmp_path, make_dir=True) is True


def test_a_probe_dir_is_made_and_removed_when_the_writer_makes_one(tmp_path):
    assert can_write(tmp_path, make_dir=True) is True
    assert list(tmp_path.iterdir()) == []


def test_a_probe_dir_that_cannot_take_a_file_is_removed_and_not_writable(tmp_path, monkeypatch):
    real = os.open

    def refuse(path, flags, *args, **kwargs):
        if os.path.dirname(path) != os.fspath(tmp_path):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", refuse)
    assert can_write(tmp_path, make_dir=True) is False
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("make_dir", [False, True])
def test_a_directory_near_the_path_length_limit_is_writable(tmp_path, monkeypatch, make_dir):
    """Linux: a directory path of 4075 bytes takes a child ``r`` but not a 29-character probe name; one made directory
    adds a component to the path. A path over the limit is refused as ``ENAMETOOLONG``, as the kernel does."""
    limit = len(os.fspath(tmp_path)) + 2 + (2 + 1 if make_dir else 0)
    real_open, real_mkdir = os.open, os.mkdir

    def too_long(path):
        if len(os.fspath(path)) > limit:
            raise OSError(errno.ENAMETOOLONG, "File name too long", os.fspath(path))

    def guarded_open(path, *args, **kwargs):
        too_long(path)
        return real_open(path, *args, **kwargs)

    def guarded_mkdir(path, *args, **kwargs):
        too_long(path)
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded_open)
    monkeypatch.setattr(os, "mkdir", guarded_mkdir)
    assert can_write(tmp_path, make_dir=make_dir) is True
    assert list(tmp_path.iterdir()) == []


def _refuse_new_folder(monkeypatch, where):
    real = os.mkdir

    def refuse(path, *args, **kwargs):
        if os.path.realpath(os.path.dirname(path)) == os.path.realpath(where):
            raise PermissionError(13, "Access is denied", os.fspath(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", refuse)


def test_a_directory_whose_new_files_deny_reading_is_writable(tmp_path, monkeypatch):
    """Windows: an inherited ACL on a new file denies FILE_READ_DATA. The writer opens write-only and succeeds, so the
    probe must not ask for read access (``mkstemp`` opens ``O_RDWR``)."""
    _refuse_new_file(monkeypatch, tmp_path, only_reading=True)
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


def test_routing_out_under_a_missing_directory_in_a_folder_only_ancestor_is_accepted(tmp_path, monkeypatch):
    """Windows: ``C:\\`` lets a standard user make a folder but not a file. The writer makes the folder and writes inside
    it, so the probe must not ask the ancestor for a file."""
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    _access_says_yes(monkeypatch)
    _refuse_new_file(monkeypatch, tmp_path)
    out = tmp_path / "results" / "routing.json"
    assert checked_out_path(str(out), "routing") == out
    assert list(tmp_path.iterdir()) == []


def test_routing_out_under_a_missing_directory_in_an_ancestor_that_refuses_a_folder_is_refused(tmp_path, monkeypatch):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    _access_says_yes(monkeypatch)
    _refuse_new_folder(monkeypatch, tmp_path)
    with pytest.raises(InputError, match="not writable"):
        checked_out_path(str(tmp_path / "results" / "routing.json"), "routing")
    assert list(tmp_path.iterdir()) == []


def test_cells_directory_that_refuses_the_write_exits_2(tmp_path, monkeypatch, capsys):
    from dokimi.commands.arms import _check_cells_dir

    (tmp_path / "cells").mkdir()
    _access_says_yes(monkeypatch)
    _refuse_new_file(monkeypatch, tmp_path / "cells")
    with pytest.raises(SystemExit) as exit_:
        _check_cells_dir(tmp_path / "cells")
    assert exit_.value.code == 2
    assert capsys.readouterr().err == (
        f"Error: cannot write {tmp_path / 'cells'}: {tmp_path / 'cells'} is not a writable directory\n")


def test_cells_directory_missing_in_an_ancestor_that_refuses_a_folder_exits_2(tmp_path, monkeypatch, capsys):
    from dokimi.commands.arms import _check_cells_dir

    _access_says_yes(monkeypatch)
    _refuse_new_folder(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exit_:
        _check_cells_dir(tmp_path / "cells")
    assert exit_.value.code == 2
    assert capsys.readouterr().err == (
        f"Error: cannot write {tmp_path / 'cells'}: {tmp_path} is not a writable directory\n")


def test_cells_directory_missing_in_a_folder_only_ancestor_passes(tmp_path, monkeypatch):
    from dokimi.commands.arms import _check_cells_dir

    _access_says_yes(monkeypatch)
    _refuse_new_file(monkeypatch, tmp_path)
    _check_cells_dir(tmp_path / "out" / "cells")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("cells_exist", [False, True])
def test_existing_out_dir_that_refuses_a_file_exits_2_whether_or_not_cells_exist(tmp_path, monkeypatch, capsys, cells_exist):
    """Windows: an out dir that lets a folder be made and not a file would fail the rollup after every cell is paid for."""
    from dokimi.commands.arms import _check_cells_dir

    if cells_exist:
        (tmp_path / "cells").mkdir()
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
