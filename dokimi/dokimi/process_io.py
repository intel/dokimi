# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A pipe is owned by its reader or caller cleanup, even during interrupted startup."""

import os
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import IO, Protocol

_POSIX = sys.platform != "win32"


def path_text(raw: bytes) -> str:
    """A file name git printed, as the path the file system opens: on POSIX a non-UTF-8 byte stays one
    (``os.fsdecode``), on Windows it becomes U+FFFD. Every reader of a ``-z`` listing decodes a path this way."""
    return os.fsdecode(raw) if _POSIX else raw.decode("utf-8", errors="replace")


def path_text_is_lossy(raw: bytes) -> bool:
    """Whether :func:`path_text` loses bytes of ``raw``: on Windows a name that is not valid UTF-8 reads as
    one with U+FFFD, which no longer says which file it was."""
    return not _POSIX and path_text(raw).encode("utf-8") != raw


def exact_path_text(raw: bytes) -> str | None:
    """:func:`path_text` for a map keyed by file name: None for a name Windows cannot decode, since the replaced name is
    another file's key (``l\\xffint`` and ``l\\ufffdint`` would share one). It decodes as Windows' ``os.fsdecode`` does,
    UTF-8 with ``surrogatepass``, so an encoded lone surrogate (``\\xed\\xa0\\x80``) is the name of its file."""
    if _POSIX:
        return os.fsdecode(raw)
    try:
        return raw.decode("utf-8", "surrogatepass")
    except UnicodeDecodeError:
        return None


def disk_path_text(raw: bytes) -> str:
    """A file name git listed from the disk (untracked, ignored), as the name the file system gives that file:
    :func:`exact_path_text`, so on Windows a lone-surrogate name matches its file; :func:`path_text` only for a name
    Windows cannot decode, which no file system call returns."""
    exact = exact_path_text(raw)
    return path_text(raw) if exact is None else exact


def worktree_changed(status: bytes) -> set[str]:
    """The tracked paths whose work tree differs from the index, from ``git status --porcelain=v1 -z``: an entry is
    ``XY path``, and a rename or copy in the index is followed by its origin path."""
    changed: set[str] = set()
    items = iter(status.split(b"\0"))
    for entry in items:
        if len(entry) < 4:
            continue
        if b"R" in entry[:2] or b"C" in entry[:2]:
            next(items, None)
        if entry[1:2] != b" ":
            if (name := exact_path_text(entry[3:])) is not None:  # a replaced name is the key of another file
                changed.add(name)
    return changed


def flagged(tags: bytes) -> set[str]:
    """The index entries git does not check against the disk: skip-worktree (``S``) and assume-unchanged (lower case)."""
    names = (exact_path_text(entry[2:]) for entry in tags.split(b"\0") if entry[:1] == b"S" or entry[:1].islower())
    return {name for name in names if name is not None}


class PipeReader:
    def __init__(self, pipe: IO[bytes], consume: Callable[[bytes], None], chunk_size: int):
        self.pipe, self.consume, self.chunk_size = pipe, consume, chunk_size
        self._lock = threading.Lock()
        self._claimed = self._cancelled = False

    def pump(self) -> None:
        with self._lock:
            if self._cancelled:
                return
            self._claimed = True
        try:
            while chunk := self.pipe.read1(self.chunk_size):  # type: ignore[attr-defined]
                self.consume(chunk)
        except (OSError, ValueError):
            pass
        finally:
            with suppress(OSError):
                self.pipe.close()

    def close_unclaimed(self) -> None:
        with self._lock:
            if not self._claimed:
                self._cancelled = True
                with suppress(OSError):
                    self.pipe.close()


class PipeReaderOwner(Protocol):
    _dokimi_pipe_readers: dict[IO[bytes], PipeReader]
