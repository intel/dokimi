# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A symlink loop on Windows is EINVAL with WinError 1921, not ELOOP (#529): each site that tells a loop
apart from another error reads both the same way."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from dokimi.utils import InputError, is_symlink_loop


class _WindowsError(OSError):
    """An ``OSError`` as Windows raises it: ``errno`` from the Windows code, which ``winerror`` keeps."""

    winerror: int


def _windows_error(winerror: int) -> OSError:
    exc = _WindowsError(errno.EINVAL, "windows error")
    exc.winerror = winerror
    return exc


def _loop_error(kind: str) -> OSError:
    return OSError(errno.ELOOP, "Too many levels of symbolic links") if kind == "posix" else _windows_error(1921)


LOOPS = pytest.mark.parametrize("kind", ["posix", "windows"])


def test_is_symlink_loop_tells_a_loop_from_every_other_error():
    assert is_symlink_loop(_loop_error("posix")) and is_symlink_loop(_loop_error("windows"))
    assert not is_symlink_loop(_windows_error(123))  # ERROR_INVALID_NAME: also EINVAL, not a loop
    assert not is_symlink_loop(OSError(errno.EINVAL, "invalid argument"))
    assert not is_symlink_loop(OSError(errno.ENOENT, "missing"))


@LOOPS
def test_runs_lookup_reads_a_looping_path_as_absent(tmp_path, monkeypatch, kind):
    from dokimi.runs.scan import lookup

    def stat(path, *args, **kwargs):
        raise _loop_error(kind)

    monkeypatch.setattr(os, "stat", stat)
    assert lookup(tmp_path / "loop") is None


def test_runs_lookup_still_raises_another_windows_error(tmp_path, monkeypatch):
    from dokimi.runs.scan import lookup

    def stat(path, *args, **kwargs):
        raise _windows_error(123)

    monkeypatch.setattr(os, "stat", stat)
    with pytest.raises(OSError) as raised:
        lookup(tmp_path / "bad")
    assert getattr(raised.value, "winerror", None) == 123


@LOOPS
def test_runs_root_that_loops_does_not_exist(tmp_path, monkeypatch, kind):
    from dokimi.runs.scan import RunsRoot

    def stat(path, *args, **kwargs):
        raise _loop_error(kind)

    monkeypatch.setattr(os, "stat", stat)
    assert RunsRoot(tmp_path / "results", from_env=False).exists() is False


@LOOPS
def test_repo_links_real_is_none_for_a_looping_path(tmp_path, monkeypatch, kind):
    from dokimi import repo_links

    def stat(self, *args, **kwargs):
        raise _loop_error(kind)

    monkeypatch.setattr(Path, "stat", stat)
    assert repo_links._real(tmp_path / "loop") is None


def test_repo_links_real_keeps_the_path_on_another_windows_error(tmp_path, monkeypatch):
    from dokimi import repo_links

    def stat(self, *args, **kwargs):
        raise _windows_error(123)

    monkeypatch.setattr(Path, "stat", stat)
    assert repo_links._real(tmp_path / "bad") == (tmp_path / "bad").resolve()


class _Entry:
    """A directory entry whose ``is_dir`` fails: a symlink the file system cannot resolve."""

    name = "loop"

    def __init__(self, error: OSError):
        self.error = error

    def is_symlink(self) -> bool:
        return True

    def is_dir(self) -> bool:
        raise self.error


class _Scan:
    def __init__(self, entries):
        self.entries = entries

    def __enter__(self):
        return iter(self.entries)

    def __exit__(self, *exc):
        return False


@LOOPS
@pytest.mark.parametrize("strict", [False, True])
def test_a_looping_entry_is_listed_as_a_link(tmp_path, monkeypatch, kind, strict):
    from dokimi import repo_glob

    monkeypatch.setattr(os, "scandir", lambda base: _Scan([_Entry(_loop_error(kind))]))
    assert repo_glob._listing(tmp_path, strict=strict) == [("loop", False, True)]


@LOOPS
def test_a_looping_directory_has_no_entries_even_in_strict_mode(tmp_path, monkeypatch, kind):
    from dokimi import repo_glob

    def scandir(base):
        raise _loop_error(kind)

    monkeypatch.setattr(os, "scandir", scandir)
    assert repo_glob._listing(tmp_path, strict=True) == []


def test_strict_listing_still_raises_another_windows_error_of_an_entry(tmp_path, monkeypatch):
    from dokimi import repo_glob

    monkeypatch.setattr(os, "scandir", lambda base: _Scan([_Entry(_windows_error(123))]))
    with pytest.raises(OSError):
        repo_glob._listing(tmp_path, strict=True)
    assert repo_glob._listing(tmp_path) == []


@LOOPS
def test_routing_out_that_loops_is_refused_as_a_loop(tmp_path, monkeypatch, kind):
    from dokimi.commands.novelty_routing_inputs import checked_out_path

    out = tmp_path / "routing.json"
    real_stat = os.stat

    def stat(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(out):
            raise _loop_error(kind)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    with pytest.raises(InputError, match="is a symlink loop"):
        checked_out_path(str(out), "routing")
