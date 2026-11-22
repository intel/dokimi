# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Publish a small text file in one step: a reader never sees half of it (no fsync, so a power loss may)."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import secrets
import stat
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any

from dokimi.process_io import utf8_bytes


class _OwnerRefused(PermissionError):
    """Root could not give a new inode the owner, group or mode of the file it replaces, so the file is left as it was."""


def write_text_atomic(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` through a random-named ``.tmp`` sibling, then ``os.replace``.

    The tmp name keeps at most 32 characters of the file name, so it stays under 170 bytes
    however long the file name is. The tmp file is created new (``O_EXCL``), so a link planted
    at its name is never followed. A file that existed keeps its mode and, where the user may give it, its group (root also keeps the owner, and raises ``PermissionError`` with the old file kept when it may not chown the owner or the group, or set the mode afterwards). Concurrent writers to one
    path each publish a whole file; the last one wins. A failed write removes its tmp file and
    leaves the file at ``path`` as it was. Missing parent directories are created.
    """
    _publish(Path(path), lambda fd: os.fdopen(fd, "w", encoding=encoding), text)


def write_bytes_atomic(path: Path | str, data: bytes) -> None:
    """:func:`write_text_atomic` for bytes, written as they are."""
    _publish(Path(path), lambda fd: os.fdopen(fd, "wb"), data)


def write_chunks_atomic(path: Path | str, chunks: Iterable[bytes]) -> None:
    """:func:`write_bytes_atomic` for an iterable of byte chunks, taken one at a time and never held together.

    An exception from the iterable removes the tmp file and leaves the file at ``path`` as it was. A symlink at ``path``
    (dangling or not) is followed, as ``open(path, "w")`` follows it: the tmp file is made beside the link's target, which is replaced, and the link stays.
    """
    _publish(Path(os.path.realpath(path)), lambda fd: os.fdopen(fd, "wb"), chunks)


def write_bytes_in_place_or_atomic(path: Path | str, data: bytes) -> None:
    """:func:`write_bytes_atomic` for a plain file or a new one; any other target is written in place, as ``Path.write_bytes`` does.

    A symlink, a hard-linked file, a special file (``/dev/stdout``), a read-only file, a file in a read-only directory,
    a file another user owns (root excepted: it restores the owner and group, or writes in place when it may not chown), a file whose name is a mount point (``os.replace`` fails with EBUSY) is written in place, a file in a group the user is not in, a file with a POSIX access ACL or a ``user.*`` extended attribute and a file in a directory with a default ACL get what an in-place write gives them: the link target changes, the other names see the data, the device gets it, a
    writable file in a read-only directory is rewritten, a file keeps its owner, group and ACL, and a read-only file raises ``PermissionError`` with its old text kept. Missing parent directories are created.
    """
    path = Path(path)
    if _is_plain_writable_file(path):
        try:
            _publish(path, lambda fd: os.fdopen(fd, "wb"), data, keep_group=True)
            return
        except _OwnerRefused:
            pass
        except OSError as exc:
            if exc.errno != errno.EBUSY:
                raise
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _is_plain_writable_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and os.access(path, os.W_OK)
        and os.access(path.parent, os.W_OK | os.X_OK)
        and _can_keep_owner(info)
        and _can_keep_group(info)
        and not _has_access_acl(path)
        and not _has_user_xattr(path)
        and not _has_default_acl(path.parent)
    )


def _can_keep_owner(info: os.stat_result) -> bool:
    """A new inode is the user's own; only root can give it another owner (a sticky directory forbids replacing another user's file as well)."""
    euid = getattr(os, "geteuid", lambda: 0)()
    return euid == 0 or info.st_uid == euid


def _can_keep_group(info: os.stat_result) -> bool:
    """A new inode can be given the file's group only by root or a member of that group."""
    if not hasattr(os, "getgroups") or getattr(os, "geteuid", lambda: 0)() == 0:
        return True
    return info.st_gid in (os.getegid(), *os.getgroups())


def _has_access_acl(path: Path) -> bool:
    """A POSIX access ACL (Linux) is not copied by a new inode, and its mask shows as the group bits of the mode."""
    try:
        return "system.posix_acl_access" in os.listxattr(path)
    except (AttributeError, OSError):
        return False


def _has_user_xattr(path: Path) -> bool:
    """A ``user.*`` extended attribute (Linux) is not copied by a new inode, so a rewritten report would lose it."""
    try:
        return any(name.startswith("user.") for name in os.listxattr(path))
    except (AttributeError, OSError):
        return False


def _has_default_acl(directory: Path) -> bool:
    """A default ACL (Linux) on the directory is given to a new inode, so a replaced file would gain entries it never had."""
    try:
        return "system.posix_acl_default" in os.listxattr(directory)
    except (AttributeError, OSError):
        return False


def write_json_atomic(path: Path | str, obj: Any) -> None:
    """Write ``obj`` as indented JSON (non-ASCII kept, one trailing newline) through :func:`write_bytes_atomic`.

    A value loaded from a ``\\ud83d\\ude00`` escape is joined into its character and a lone surrogate is written
    as ``?`` (:func:`dokimi.process_io.utf8_bytes`), where a strict UTF-8 encode would raise. Each line ends
    with the platform's ``os.linesep``, as :func:`write_text_atomic` writes it (``\\r\\n`` on Windows).
    """
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    write_bytes_atomic(path, utf8_bytes(text.replace("\n", os.linesep)))


def _publish(path: Path, open_tmp: Callable[[int], IO], data: str | bytes | Iterable[bytes], *, keep_group: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name[:32]}.{secrets.token_hex(8)}.tmp")
    old = _existing_stat(path)
    mode = 0o666 if old is None else stat.S_IMODE(old.st_mode)
    try:
        # O_BINARY: without it Windows writes each \n as \r\n a second time, as tempfile avoids.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp, flags, mode)
        if old is not None:
            try:
                _keep_owner_and_group(fd, old, keep_group=keep_group)
            except BaseException:
                os.close(fd)
                raise
        with open_tmp(fd) as handle:
            if isinstance(data, (str, bytes)):
                handle.write(data)
            else:
                handle.writelines(data)
        if old is not None:
            # os.open's mode is cut by the umask, and a change of owner or a write by a user without CAP_FSETID clears set-user-ID and set-group-ID: the mode is restored last.
            try:
                os.chmod(tmp, mode)
            except PermissionError as exc:
                raise _OwnerRefused(exc.errno, "root may not set the mode of the new file it gave the owner of the file it replaces") from exc
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _keep_owner_and_group(fd: int, old: os.stat_result, *, keep_group: bool = False) -> None:
    """Give the new inode the old file's group and, for root, its owner.

    A refused group change of a user who is not root leaves the group the inode got, unless ``keep_group`` (the caller falls back to an in-place write); a refused change of root (no CAP_CHOWN, or a policy that denies it), of the owner or of the group alone, raises :class:`_OwnerRefused`.
    """
    if not hasattr(os, "fchown"):
        return
    now = os.fstat(fd)
    root = getattr(os, "geteuid", lambda: 0)() == 0
    uid = old.st_uid if root and old.st_uid != now.st_uid else -1
    gid = old.st_gid if old.st_gid != now.st_gid else -1
    if (uid, gid) == (-1, -1):
        return
    try:
        os.fchown(fd, uid, gid)
    except PermissionError as exc:
        if root or keep_group:
            raise _OwnerRefused(exc.errno, "root may not give the new file the owner or group of the file it replaces") from exc


def _existing_stat(path: Path) -> os.stat_result | None:
    try:
        return path.stat()
    except FileNotFoundError:
        return None
