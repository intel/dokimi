# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Whether a path can be written, found by writing: the pre-flight check of a command that pays before it saves."""

from __future__ import annotations

import os
import secrets


def _probe_name(directory: str) -> str:
    return os.path.join(directory, f".write-probe-{secrets.token_hex(8)}")


def can_write(path: str | os.PathLike[str], *, make_dir: bool = False) -> bool:
    """True when the existing ``path`` takes a write: a file opens for append (nothing is truncated or added), a directory
    takes a new file, which is removed again if the directory lets it be (a writer needs no delete access, so a refused
    removal is not a refusal). With ``make_dir`` the writer makes a directory in ``path`` and writes inside it (Windows
    grants creating a folder and creating a file separately), so the probe makes a directory there and takes the new file
    in it; either is left behind only if the directory refuses its removal. ``os.access`` is no proof: on Windows it reads
    the read-only attribute of a file, is true for any directory, and does not see an ACL that denies the write."""
    try:
        if os.path.isdir(path):
            where = os.fspath(path)
            probe_dir = _probe_name(where) if make_dir else None
            if probe_dir is not None:
                os.mkdir(probe_dir)
                where = probe_dir
            name = _probe_name(where)
            try:
                # write-only and not delete-on-close, as the writer opens it: mkstemp asks for read access, TemporaryFile for delete
                os.close(os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            except OSError:
                if probe_dir is not None:
                    os.rmdir(probe_dir)
                raise
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
