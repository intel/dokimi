# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Publish a small text file in one step: a reader never sees half of it (no fsync, so a power loss may)."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import stat
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from dokimi.process_io import utf8_bytes


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


def write_bytes_in_place_or_atomic(path: Path | str, data: bytes) -> None:
    """:func:`write_bytes_atomic` for a plain file or a new one; any other target is written in place, as ``Path.write_bytes`` does.

    A symlink, a hard-linked file, a special file (``/dev/stdout``), a read-only file and a file in a read-only directory
    get what an in-place write gives them: the link target changes, the other names see the data, the device gets it, a
    writable file in a read-only directory is rewritten, and a read-only file raises ``PermissionError`` with its old text kept. Missing parent directories are created.
    """
    path = Path(path)
    if _is_plain_writable_file(path):
        _publish(path, lambda fd: os.fdopen(fd, "wb"), data)
        return
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
    )


def write_json_atomic(path: Path | str, obj: Any) -> None:
    """Write ``obj`` as indented JSON (non-ASCII kept, one trailing newline) through :func:`write_bytes_atomic`.

    A value loaded from a ``\\ud83d\\ude00`` escape is joined into its character and a lone surrogate is written
    as ``?`` (:func:`dokimi.process_io.utf8_bytes`), where a strict UTF-8 encode would raise. Each line ends
    with the platform's ``os.linesep``, as :func:`write_text_atomic` writes it (``\\r\\n`` on Windows).
    """
    text = json.dumps(obj, indent=2, ensure_ascii=False) + "\n"
    write_bytes_atomic(path, utf8_bytes(text.replace("\n", os.linesep)))


def _publish(path: Path, open_tmp: Callable[[int], IO], data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name[:32]}.{secrets.token_hex(8)}.tmp")
    mode = _existing_mode(path)
    try:
        # O_BINARY: without it Windows writes each \n as \r\n a second time, as tempfile avoids.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(tmp, flags, 0o666 if mode is None else mode)
        if mode is not None:
            # os.open's mode is cut by the umask; the data is written only after the mode is restored.
            try:
                os.chmod(tmp, mode)
            except BaseException:
                os.close(fd)
                raise
        with open_tmp(fd) as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _existing_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None
