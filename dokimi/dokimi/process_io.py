# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A pipe is owned by its reader or caller cleanup, even during interrupted startup.

read_spool reads back a spooled capture; join_capture joins the head and tail of a capture that was cut.
"""

import json
import os
import re
import sys
from stat import S_ISFIFO
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import IO, Protocol

_POSIX = sys.platform != "win32"
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_ASTRAL = re.compile("[\U00010000-\U0010ffff]")


def joined_pairs(text: str) -> str:
    """*text* with each high surrogate followed by a low one (a YAML double-quoted ``"\\ud83d\\ude00"`` escape, which PyYAML
    loads as two code points; ``json`` joins the pair itself) joined into its character; a lone surrogate stays, for ``errors="replace"`` to write as ``?``."""
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def utf8_bytes(text: str) -> bytes:
    """*text* as UTF-8 with its surrogate pairs joined (:func:`joined_pairs`) and a lone surrogate sent as ``?``."""
    return joined_pairs(text).encode("utf-8", "replace")


def json_utf8_bytes(text: str) -> bytes:
    """JSON *text* (from ``json.dumps(..., ensure_ascii=False)``) as UTF-8 with its surrogate pairs joined and each lone
    surrogate kept as its ``\\udXXX`` escape, which ``json.loads`` reads back: two object keys never collapse into one.
    Raises ValueError when joining makes two keys of one object equal (a pair key beside its character): a reader keeps one."""
    joined = joined_pairs(text)
    if joined != text:
        json.loads(joined, object_pairs_hook=_distinct_keys)
    return _LONE_SURROGATE.sub(lambda m: f"\\u{ord(m.group()):04x}", joined).encode("utf-8")


def _distinct_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs if _ASTRAL.search(key)]
    if len(set(keys)) < len(keys):
        raise ValueError("two object keys are the same once their surrogate pairs are joined; a reader would keep one")
    return dict(pairs)


def text_file_bytes(text: str) -> bytes:
    """What ``Path.write_text(text, encoding="utf-8")`` writes on this platform (``\\n`` as ``os.linesep``) for text that
    encodes; a lone surrogate is a ``?`` and a pair is joined (:func:`utf8_bytes`), where ``write_text`` raises."""
    return utf8_bytes(text if os.linesep == "\n" else text.replace("\n", os.linesep))


def argv_text(text: str) -> str:
    """An argv entry as the child gets it: pairs joined (:func:`joined_pairs`), and each character ``os.fsencode`` cannot
    encode (a lone surrogate) a ``?``. On POSIX U+DC80..U+DCFF stay: ``os.fsdecode`` makes them of a non-UTF-8 file name."""
    joined = joined_pairs(text)
    try:
        os.fsencode(joined)
    except UnicodeEncodeError:
        return "".join("?" if 0xD800 <= ord(char) <= 0xDFFF and not _fs_encodable(char) else char for char in joined)
    return joined


def _fs_encodable(char: str) -> bool:
    try:
        os.fsencode(char)
    except UnicodeEncodeError:
        return False
    return True


def path_text(raw: bytes) -> str:
    """A file name git printed, as the path the file system opens: on POSIX a non-UTF-8 byte stays one
    (``os.fsdecode``), on Windows it becomes U+FFFD. Every reader of a ``-z`` listing decodes a path this way."""
    return os.fsdecode(raw) if _POSIX else raw.decode("utf-8", errors="replace")


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


TRUNCATION_MARKER = "\n[... {dropped} bytes not kept: output over the capture limit ...]\n"


def join_capture(head: bytes, tail: bytes, total: int) -> tuple[bytes, bool]:
    dropped = total - len(head) - len(tail)
    if dropped <= 0:
        return head + tail, False
    return head + TRUNCATION_MARKER.format(dropped=dropped).encode("utf-8") + tail, True


def read_spool(path: str | os.PathLike[str], limit: int | None) -> tuple[bytes, bool]:
    """A spooled stream's bounded head and tail, read back without loading the whole file. A FIFO is not read back:
    its reader is at the other end, and a read here waits for every writer to close, the caller's included."""
    if S_ISFIFO(os.stat(path).st_mode):
        return b"", False
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if limit is None or size <= limit:
            return fh.read(), False
        head = fh.read(limit // 2)
        tail_len = limit - limit // 2
        fh.seek(size - tail_len)
        return join_capture(head, fh.read(tail_len), size)
