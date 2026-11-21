# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A report replaced through a new inode keeps its owner, group and access ACL, or is written in place.

``write_bytes_in_place_or_atomic`` publishes a new inode only when it can give it what the old file had.
"""

import errno
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from dokimi.atomic_write import _keep_owner_and_group, write_bytes_atomic, write_bytes_in_place_or_atomic

pytestmark = pytest.mark.skipif(not hasattr(os, "geteuid") or not hasattr(os, "fchown"), reason="needs POSIX owners")


def _report(tmp_path: Path) -> Path:
    target = tmp_path / "r.md"
    target.write_bytes(b"old")
    return target


def _fstat_of_another_owner(monkeypatch):
    """``os.fstat`` as if the new inode belonged to another user and group than the process."""
    real = os.fstat

    def other(fd):
        values = list(real(fd))
        values[4] += 1
        values[5] += 1
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", other)


def test_a_file_another_user_owns_is_written_in_place(tmp_path, monkeypatch):
    target = _report(tmp_path)
    before = target.stat().st_ino
    monkeypatch.setattr(os, "geteuid", lambda: target.stat().st_uid + 1)
    write_bytes_in_place_or_atomic(target, b"new")
    assert (target.read_bytes(), target.stat().st_ino) == (b"new", before)


def test_root_gives_the_new_inode_the_owner_of_the_file_it_replaces(tmp_path, monkeypatch):
    target = _report(tmp_path)
    before = target.stat().st_ino
    owner = target.stat().st_uid
    chowned = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    _fstat_of_another_owner(monkeypatch)
    write_bytes_in_place_or_atomic(target, b"new")
    assert target.read_bytes() == b"new" and target.stat().st_ino != before
    assert chowned == [(owner, target.stat().st_gid)]


def test_a_user_who_is_not_root_does_not_try_to_change_the_owner(tmp_path, monkeypatch):
    target = _report(tmp_path)
    old = target.stat()
    chowned = []
    monkeypatch.setattr(os, "geteuid", lambda: old.st_uid + 1)
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: chowned.append((uid, gid)))
    _fstat_of_another_owner(monkeypatch)
    fd = os.open(tmp_path / "new", os.O_WRONLY | os.O_CREAT)
    try:
        _keep_owner_and_group(fd, old)
    finally:
        os.close(fd)
    assert chowned == [(-1, old.st_gid)]


def test_a_replaced_report_keeps_its_set_user_id_bit_through_the_ownership_change(tmp_path, monkeypatch):
    target = _report(tmp_path)
    target.chmod(0o4640)
    before = target.stat().st_ino
    real_fchmod = os.fchmod

    def chown_clearing_special_bits(fd, uid, gid):
        real_fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) & 0o777)

    monkeypatch.setattr(os, "fchown", chown_clearing_special_bits)
    _fstat_of_another_owner(monkeypatch)
    write_bytes_in_place_or_atomic(target, b"new")
    assert target.stat().st_ino != before
    assert (target.read_bytes(), stat.S_IMODE(target.stat().st_mode)) == (b"new", 0o4640)


def test_a_system_without_fchown_still_replaces_the_report(tmp_path, monkeypatch):
    target = _report(tmp_path)
    before = target.stat().st_ino
    monkeypatch.delattr(os, "fchown")
    _fstat_of_another_owner(monkeypatch)
    write_bytes_in_place_or_atomic(target, b"new")
    assert (target.read_bytes(), target.stat().st_ino != before) == (b"new", True)
    assert [p.name for p in tmp_path.iterdir()] == ["r.md"]


def test_a_failed_group_restore_closes_the_descriptor_and_keeps_the_old_report(tmp_path, monkeypatch):
    target = _report(tmp_path)
    opened, closed = [], []
    real_open, real_close = os.open, os.close

    def spy_open(name, flag, mode=0o777, *args, **kwargs):
        opened.append(real_open(name, flag, mode, *args, **kwargs))
        return opened[-1]

    def spy_close(fd):
        closed.append(fd)
        return real_close(fd)

    def eio(*args, **kwargs):
        raise OSError(errno.EIO, "injected fchown failure")

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(os, "fchown", eio)
    _fstat_of_another_owner(monkeypatch)
    with pytest.raises(OSError, match="injected fchown failure"):
        write_bytes_in_place_or_atomic(target, b"new")
    assert opened and set(opened) <= set(closed)
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["r.md"]


def _root_denied_chown(monkeypatch):
    """Root without CAP_CHOWN: ``fchown`` is refused with EPERM."""

    def denied(fd, uid, gid):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", denied)
    _fstat_of_another_owner(monkeypatch)


def test_root_without_the_right_to_chown_writes_the_report_in_place(tmp_path, monkeypatch):
    target = _report(tmp_path)
    before = target.stat()
    _root_denied_chown(monkeypatch)
    write_bytes_in_place_or_atomic(target, b"new")
    after = target.stat()
    assert (target.read_bytes(), after.st_ino, after.st_uid) == (b"new", before.st_ino, before.st_uid)
    assert [p.name for p in tmp_path.iterdir()] == ["r.md"]


def test_root_without_the_right_to_chown_does_not_replace_a_file_atomically(tmp_path, monkeypatch):
    target = _report(tmp_path)
    _root_denied_chown(monkeypatch)
    with pytest.raises(PermissionError):
        write_bytes_atomic(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["r.md"]


def test_a_file_with_an_access_acl_is_written_in_place(tmp_path, monkeypatch):
    target = _report(tmp_path)
    before = target.stat().st_ino
    monkeypatch.setattr(os, "listxattr", lambda path: ["system.posix_acl_access"], raising=False)
    write_bytes_in_place_or_atomic(target, b"new")
    assert (target.read_bytes(), target.stat().st_ino) == (b"new", before)


def test_a_file_in_a_directory_with_a_default_acl_is_written_in_place(tmp_path, monkeypatch):
    target = _report(tmp_path)
    before = target.stat().st_ino
    monkeypatch.setattr(os, "listxattr", lambda path: ["system.posix_acl_default"] if Path(path) == tmp_path else [], raising=False)
    write_bytes_in_place_or_atomic(target, b"new")
    assert (target.read_bytes(), target.stat().st_ino) == (b"new", before)


@pytest.mark.skipif(shutil.which("setfacl") is None or shutil.which("getfacl") is None, reason="needs setfacl")
def test_a_real_default_acl_of_the_directory_is_not_given_to_a_rewritten_report(tmp_path):
    target = _report(tmp_path)
    target.chmod(0o640)
    acl = subprocess.run(["setfacl", "-m", "d:u:12345:r-x", str(tmp_path)], capture_output=True, text=True)
    if acl.returncode != 0:
        pytest.skip("the file system takes no ACL")
    write_bytes_in_place_or_atomic(target, b"new")
    shown = subprocess.run(["getfacl", "-c", str(target)], capture_output=True, text=True).stdout
    assert target.read_bytes() == b"new"
    assert "12345" not in shown


def test_a_file_without_an_acl_or_on_a_system_without_xattrs_is_still_replaced(tmp_path, monkeypatch):
    target = _report(tmp_path)
    before = target.stat().st_ino
    monkeypatch.setattr(os, "listxattr", lambda path: (_ for _ in ()).throw(OSError(errno.ENOTSUP, "no xattrs")), raising=False)
    write_bytes_in_place_or_atomic(target, b"new")
    assert target.read_bytes() == b"new"
    assert target.stat().st_ino != before


@pytest.mark.skipif(shutil.which("setfacl") is None or shutil.which("getfacl") is None, reason="needs setfacl")
def test_a_real_access_acl_survives_a_rewrite(tmp_path):
    target = _report(tmp_path)
    acl = subprocess.run(["setfacl", "-m", "u:0:r", str(target)], capture_output=True, text=True)
    if acl.returncode != 0:
        pytest.skip("the file system takes no ACL")
    write_bytes_in_place_or_atomic(target, b"new")
    shown = subprocess.run(["getfacl", "-c", str(target)], capture_output=True, text=True).stdout
    assert target.read_bytes() == b"new"
    assert "user:root:r--" in shown
