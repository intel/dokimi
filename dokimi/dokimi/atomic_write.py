# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Publish a small text file in one step: a reader never sees half of it (no fsync, so a power loss may)."""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import IO


def write_text_atomic(path: Path | str, text: str, *, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` through a random-named ``.tmp`` sibling, then ``os.replace``.

    The tmp name keeps at most 32 characters of the file name, so it stays under 170 bytes
    however long the file name is. The tmp file is created new (``O_EXCL``), so a link planted
    at its name is never followed. A file that existed keeps its mode. Concurrent writers to one
    path each publish a whole file; the last one wins. A failed write removes its tmp file and
    leaves the file at ``path`` as it was. Missing parent directories are created.
    """
    _publish(Path(path), lambda fd: os.fdopen(fd, "w", encoding=encoding), text)


def write_bytes_atomic(path: Path | str, data: bytes) -> None:
    """:func:`write_text_atomic` for bytes, written as they are."""
    _publish(Path(path), lambda fd: os.fdopen(fd, "wb"), data)


def _publish(path: Path, open_tmp: Callable[[int], IO], data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name[:32]}.{secrets.token_hex(8)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        with open_tmp(fd) as handle:
            handle.write(data)
        if path.exists():
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
