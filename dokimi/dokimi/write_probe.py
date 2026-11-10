# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Whether a path can be written, found by writing: the pre-flight check of a command that pays before it saves."""

from __future__ import annotations

import os
import tempfile


def can_write(path: str | os.PathLike[str]) -> bool:
    """True when the existing ``path`` takes a write: a file opens for append (nothing is truncated or added), a directory
    takes a new file, which is removed again if the directory lets it be (a writer needs no delete access, so a refused
    removal is not a refusal). ``os.access`` is no proof: on Windows it reads the read-only attribute of a file, is true
    for any directory, and does not see an ACL that denies the write."""
    try:
        if os.path.isdir(path):
            # not TemporaryFile: on Windows that opens delete-on-close, which needs the delete access a plain write does not
            handle, name = tempfile.mkstemp(dir=path)
            os.close(handle)
            try:
                os.unlink(name)
            except OSError:
                pass
        else:
            with open(path, "a", encoding="utf-8"):
                pass
    except OSError:  # PermissionError, or a read-only file system
        return False
    return True
