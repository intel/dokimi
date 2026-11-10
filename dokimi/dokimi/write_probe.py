# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Whether a path can be written, found by writing: the pre-flight check of a command that pays before it saves."""

from __future__ import annotations

import errno
import os
import secrets
from collections.abc import Iterator


def _probe_names(directory: str) -> Iterator[str]:
    """The long name, then short ones: a directory near the path-length limit takes a child ``r`` but not 29 more characters."""
    yield os.path.join(directory, f".write-probe-{secrets.token_hex(8)}")
    for width in (2, 1):
        yield os.path.join(directory, secrets.token_hex(width)[:width])


def _make(directory: str, *, as_dir: bool) -> str:
    """Create a probe file (write-only, exclusive) or directory in ``directory``; the first name that fits, is free and is
    allowed is the result."""
    last: OSError | None = None
    for name in _probe_names(directory):
        try:
            if as_dir:
                os.mkdir(name)
            else:
                # write-only and not delete-on-close, as the writer opens it: mkstemp asks for read access, TemporaryFile for delete
                os.close(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError as error:
            last = error
        except OSError as error:
            if error.errno != errno.ENAMETOOLONG:
                raise
            last = error
        else:
            return name
    assert last is not None
    raise last


def _make_dir_and_file(directory: str) -> tuple[str, str]:
    """Create a probe directory in ``directory`` and a probe file in it. A directory name that fits but leaves no room for
    the file inside is removed and the next, shorter name tried."""
    last: OSError | None = None
    for name in _probe_names(directory):
        try:
            os.mkdir(name)
        except FileExistsError as error:
            last = error
            continue
        except OSError as error:
            if error.errno != errno.ENAMETOOLONG:
                raise
            last = error
            continue
        try:
            return name, _make(name, as_dir=False)
        except OSError as error:
            os.rmdir(name)
            if error.errno != errno.ENAMETOOLONG:
                raise
            last = error
    assert last is not None
    raise last


def can_write(path: str | os.PathLike[str], *, make_dir: bool = False) -> bool:
    """True when the existing ``path`` takes a write: a file opens for append (nothing is truncated or added), a directory
    takes a new file, which is removed again if the directory lets it be (a writer needs no delete access, so a refused
    removal is not a refusal). With ``make_dir`` the writer makes a directory in ``path`` and writes inside it (Windows
    grants creating a folder and creating a file separately), so the probe makes a directory there and takes the new file
    in it; either is left behind only if the directory refuses its removal. ``os.access`` is no proof: on Windows it reads
    the read-only attribute of a file, is true for any directory, and does not see an ACL that denies the write."""
    try:
        if os.path.isdir(path):
            if make_dir:
                probe_dir, name = _make_dir_and_file(os.fspath(path))
            else:
                probe_dir, name = None, _make(os.fspath(path), as_dir=False)
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
