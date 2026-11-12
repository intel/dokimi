# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Whether a path can be written, found by writing: the pre-flight check of a command that pays before it saves."""

from __future__ import annotations

import errno
import os
import secrets
from collections.abc import Iterator


def _too_long(error: OSError) -> bool:
    """A name past the limit: ``ENAMETOOLONG``, or on Windows WinError 206 (ERROR_FILENAME_EXCED_RANGE), which CPython
    reports as ``ENOENT``."""
    return error.errno == errno.ENAMETOOLONG or getattr(error, "winerror", None) == 206


def _probe_names(directory: str, width: int | None) -> Iterator[str]:
    """The names to try in ``directory``. With the ``width`` of the name the writer will create, only a name of that
    length: the answer is then the writer's. Without it, the long name and then short ones, since a directory near the
    path-length limit takes a child ``r`` but not 29 more characters."""
    if width is not None:
        # the hex tail pads a long name; a name too short for the prefix is hex only
        name = f".write-probe-{secrets.token_hex(width)}" if width >= 29 else secrets.token_hex(width)
        yield os.path.join(directory, name[:width])
        return
    yield os.path.join(directory, f".write-probe-{secrets.token_hex(8)}")
    for short in (2, 1):
        yield os.path.join(directory, secrets.token_hex(short)[:short])


def _make(directory: str, width: int | None = None) -> str:
    """Create a probe file (write-only, exclusive) in ``directory``; the first name that fits, is free and is allowed is
    the result."""
    last: OSError | None = None
    for name in _probe_names(directory, width):
        try:
            # write-only and not delete-on-close, as the writer opens it: mkstemp asks for read access, TemporaryFile for delete
            os.close(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError as error:
            last = error
        except OSError as error:
            if not _too_long(error):
                raise
            last = error
        else:
            return name
    assert last is not None
    raise last


def _make_dir_and_file(directory: str, width: int | None) -> tuple[str, str]:
    """Create a probe directory in ``directory`` and a probe file of ``width`` in it. A directory name that fits but leaves
    no room for the file inside is removed and the next, shorter name tried."""
    last: OSError | None = None
    for name in _probe_names(directory, None):
        try:
            os.mkdir(name)
        except FileExistsError as error:
            last = error
            continue
        except OSError as error:
            if not _too_long(error):
                raise
            last = error
            continue
        try:
            return name, _make(name, width)
        except OSError as error:
            os.rmdir(name)
            if not _too_long(error):
                raise
            last = error
    assert last is not None
    raise last


def _name_width(child: str | None) -> int | None:
    """The length the file system limits a name by: bytes on POSIX, UTF-16 code units on Windows."""
    if child is None:
        return None
    return len(child.encode("utf-16-le", "surrogatepass")) // 2 if os.name == "nt" else len(os.fsencode(child))


def can_write(path: str | os.PathLike[str], *, make_dir: bool = False, child: str | None = None) -> bool:
    """True when the existing ``path`` takes a write: a file opens for append (nothing is truncated or added), a directory
    takes a new file, which is removed again if the directory lets it be (a writer needs no delete access, so a refused
    removal is not a refusal). With ``make_dir`` the writer makes a directory in ``path`` and writes inside it (Windows
    grants creating a folder and creating a file separately), so the probe makes a directory there and takes the new file
    in it; either is left behind only if the directory refuses its removal. With ``child``, the name of the file the
    writer will create in the directory, the probe file is as long as that name, so a directory or a name the writer
    cannot fill is refused; without it the probe falls back to a 2- and a 1-character name when a long one does not fit
    (the probe directory of ``make_dir`` always may). ``os.access`` is no proof: on Windows it reads the read-only
    attribute of a file, is true for any directory, and does not see an ACL that denies the write."""
    width = _name_width(child)
    try:
        if os.path.isdir(path):
            if make_dir:
                probe_dir, name = _make_dir_and_file(os.fspath(path), width)
            else:
                probe_dir, name = None, _make(os.fspath(path), width)
            try:
                os.unlink(name)
                if probe_dir is not None:
                    os.rmdir(probe_dir)
            except OSError:
                pass
        else:
            with open(path, "a", encoding="utf-8"):
                pass
    except OSError:  # PermissionError, or a read-only file system
        return False
    return True
