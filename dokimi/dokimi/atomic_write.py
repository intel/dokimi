# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Publish a small text file in one step: a reader never sees half of it (no fsync, so a power loss may)."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import IO, Any

from dokimi.process_io import checked_json_text, utf8_bytes


class _OwnerRefused(PermissionError):
    """Root could not give a new inode the owner, group or mode of the file it replaces, so the file is left as it was."""


def write_text_atomic(path: Path | str, text: str, *, encoding: str = "utf-8", new_mode: int = 0o666) -> None:
    """Write ``text`` to ``path`` through a random-named ``.tmp`` sibling, then ``os.replace``.

    The tmp name keeps at most 32 characters of the file name, so it stays under 170 bytes
    however long the file name is. The tmp file is created new (``O_EXCL``), so a link planted
    at its name is never followed. A file that existed keeps its mode and, where the user may give it, its group (root also keeps the owner, and raises ``PermissionError`` with the old file kept when it may not chown the owner or the group, or set the mode afterwards). Concurrent writers to one
    path each publish a whole file; the last one wins. A failed write removes its tmp file and
    leaves the file at ``path`` as it was. Missing parent directories are created.
    A file that did not exist, or a symlink at ``path`` (replaced, its target untouched), is created with ``new_mode`` cut by the umask (``0o666`` by default; ``0o600`` for a private one).
    """
    _publish(Path(path), lambda fd: os.fdopen(fd, "w", encoding=encoding), text, new_mode=new_mode)


def write_bytes_atomic(path: Path | str, data: bytes) -> None:
    """:func:`write_text_atomic` for bytes, written as they are."""
    _publish(Path(path), lambda fd: os.fdopen(fd, "wb"), data)


def write_chunks_atomic(path: Path | str, chunks: Iterable[bytes]) -> None:
    """:func:`write_bytes_atomic` for an iterable of byte chunks, taken one at a time and never held together.

    An exception from the iterable removes the tmp file and leaves the file at ``path`` as it was. A symlink at ``path``
    (dangling or not) is followed, as ``open(path, "w")`` follows it: the tmp file is made beside the link's target, which is replaced, and the link stays;
    a link into a missing directory raises ``FileNotFoundError`` and makes no directory.
    A target that is not a plain file (see :func:`write_bytes_in_place_or_atomic`: hard-linked, special, read-only,
    in a read-only directory, with a ``user.*`` attribute, ...) is written in place, as ``open(path, "w")`` writes it,
    after every chunk is taken, so an exception from the iterable still leaves it as it was; the chunks wait in a
    temporary file on disk, not in memory. A plain file takes no second copy: the tmp file beside it is copied into it in place
    when its name is a mount point (``os.replace`` fails with EBUSY) or root may not set its mode, and a user denied the group of
    the file it replaces writes it in place.
    """
    target = Path(os.path.realpath(path))
    # A link into a missing directory fails as open(path, "w") fails: the target's directory is not made.
    taken = iter(chunks)
    if not (os.path.islink(path) and not target.parent.is_dir()) and _is_plain_writable_file(target):
        try:
            # The tmp file beside the target is the only copy: a refusal after it is written, or EBUSY, copies it in place.
            _publish(target, lambda fd: os.fdopen(fd, "wb"), taken, keep_group=True, copy_in_place=True)
            return
        except _OwnerRefused:
            # Raised before the first chunk is taken, so the chunks are still all to come.
            pass
    with tempfile.TemporaryFile() as staged:
        staged.writelines(taken)
        staged.seek(0)
        with open(path, "wb") as handle:
            shutil.copyfileobj(staged, handle)


_RENAME_REPLACES_READ_ONLY = os.name != "nt"  # Windows os.replace refuses a read-only destination


def write_bytes_in_place_or_atomic(path: Path | str, data: bytes, *, replace_read_only: bool = False) -> None:
    """:func:`write_bytes_atomic` for a plain file or a new one; any other target is written in place, as ``Path.write_bytes`` does.

    With ``replace_read_only`` a read-only regular file in a writable directory is replaced by rename, plain or not (hard-linked, with a ``user.*`` attribute or an ACL, another user's), as ``os.replace`` replaces it: the new file keeps its mode, takes the group it may have, and has none of the other names, attributes or ACL (not on Windows, whose ``os.replace`` refuses a read-only destination: there it is written in place and refused; see :func:`publishes_by_rename`).

    A symlink, a hard-linked file, a special file (``/dev/stdout``), a read-only file, a file in a read-only directory,
    a file another user owns (root excepted: it restores the owner and group, or writes in place when it may not chown), a file whose name is a mount point (``os.replace`` fails with EBUSY) is written in place, a file in a group the user is not in, a file with a POSIX access ACL or a ``user.*`` extended attribute and a file in a directory with a default ACL get what an in-place write gives them: the link target changes, the other names see the data, the device gets it, a
    writable file in a read-only directory is rewritten, a file keeps its owner, group and ACL, and a read-only file raises ``PermissionError`` with its old text kept. Missing parent directories are created.
    """
    path = Path(path)
    if publishes_by_rename(path, replace_read_only=replace_read_only):
        try:
            # A read-only file has no in-place fallback that works, so a group the user may not give it is left to the new file, as ``os.replace`` leaves it.
            _publish(path, lambda fd: os.fdopen(fd, "wb"), data, keep_group=_is_plain_writable_file(path))
            return
        except _OwnerRefused:
            pass
        except OSError as exc:
            if exc.errno != errno.EBUSY:
                raise
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def publishes_by_rename(path: Path, *, replace_read_only: bool = False) -> bool:
    """Whether :func:`write_bytes_in_place_or_atomic` publishes ``path`` by a rename (else it writes it in place)."""
    return _is_plain_writable_file(path) or (replace_read_only and _RENAME_REPLACES_READ_ONLY and _is_read_only_file_in_writable_dir(path))


def _is_read_only_file_in_writable_dir(path: Path) -> bool:
    """A regular file the user may not write, in a directory the user may change (a sticky one only for its owner's or the user's own file): an in-place write is refused, a rename is not."""
    info = path.lstat()
    if not (stat.S_ISREG(info.st_mode) and not os.access(path, os.W_OK) and os.access(path.parent, os.W_OK | os.X_OK)):
        return False
    euid = getattr(os, "geteuid", lambda: 0)()
    parent = path.parent.stat()
    return not (parent.st_mode & stat.S_ISVTX) or euid in (0, info.st_uid, parent.st_uid)


def tmp_sibling(path: Path) -> Path:
    """The random tmp name a publish of ``path`` writes first: beside it, with at most 32 characters of its name."""
    return path.with_name(f".{path.name[:32]}.{secrets.token_hex(8)}.tmp")


def _is_plain_writable_file(path: Path) -> bool:
    return _is_plain_file(path) and (not os.path.lexists(path) or os.access(path, os.W_OK))


def _is_plain_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return True
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
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
    with the platform's ``os.linesep``, as :func:`write_text_atomic` writes it (``\\r\\n`` on Windows). Raises ValueError,
    writing nothing, for an object that would hold two equal keys in the bytes written (:func:`dokimi.process_io.checked_json_text`):
    a lone-surrogate key beside the key ``?`` is one of them.
    """
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    data = utf8_bytes(text.replace("\n", os.linesep))
    checked_json_text(data.decode("utf-8"))
    write_bytes_atomic(path, data)


def _publish(path: Path, open_tmp: Callable[[int], IO], data: str | bytes | Iterable[bytes], *, keep_group: bool = False,
             copy_in_place: bool = False, new_mode: int = 0o666) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_sibling(path)
    old = _existing_stat(path)
    mode = new_mode if old is None else stat.S_IMODE(old.st_mode)
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
        try:
            if old is not None:
                # The parent's default ACL gave the tmp file an access ACL the old file never had; its mask would keep that entry effective after the chmod.
                with contextlib.suppress(AttributeError, OSError):
                    os.removexattr(tmp, "system.posix_acl_access")
                # os.open's mode is cut by the umask, and a change of owner or a write by a user without CAP_FSETID clears set-user-ID and set-group-ID: the mode is restored last.
                try:
                    os.chmod(tmp, mode)
                except PermissionError as exc:
                    raise _OwnerRefused(exc.errno, "root may not set the mode of the new file it gave the owner of the file it replaces") from exc
            os.replace(tmp, path)
        except OSError as exc:
            if not copy_in_place or not (isinstance(exc, _OwnerRefused) or exc.errno == errno.EBUSY):
                raise
            # The tmp file holds all the data and is the user's own: make it readable, as a write-only destination made it write-only.
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            with open(tmp, "rb") as staged, open(path, "wb") as handle:
                shutil.copyfileobj(staged, handle)
            tmp.unlink()
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
        # A link is replaced by the rename, so it is the file replaced: its target's mode and group are not kept.
        return None if path.is_symlink() else path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            return None
        raise
