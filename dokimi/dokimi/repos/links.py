# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Walking a path in a working tree one component at a time, as a checkout does (#1084)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from dokimi.repos.source_errors import SourceArgumentError, _NoSuchPath

_JUNCTION_TAG = 0xA0000003  # IO_REPARSE_TAG_MOUNT_POINT; the stat module has it only on Windows


class _LinkLoop(SourceArgumentError):
    """More than 40 links followed on one path: no file, as in a checkout (ELOOP)."""


def _parts(text: str) -> list[str]:
    """The components of a path as the file system reads them: ``\\`` separates only where ``os.sep`` is it."""
    return text.replace("\\", "/").split("/") if os.sep == "\\" else text.split("/")


def _is_link(path: Path) -> bool:
    """A symlink, or on Windows a junction: both send the walk somewhere else. Read from ``lstat``, as
    ``Path.is_junction`` is new in Python 3.12."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) == _JUNCTION_TAG


def _link_target(path: Path) -> str:
    """The link's text; on Windows without the ``\\\\?\\`` prefix, and ``\\\\?\\UNC\\server`` as the share ``\\\\server``."""
    text = os.readlink(path)
    if os.name == "nt" and text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    return text[4:] if os.name == "nt" and text.startswith("\\\\?\\") else text


def _rooted(text: str) -> bool:
    """Whether ``text`` is read from outside the directory it is in: absolute, or on Windows with a drive
    (``Q:x`` is relative to the drive's current directory) or a leading separator (``\\x`` is on the current drive)."""
    return os.path.isabs(text) or (os.name == "nt" and (bool(os.path.splitdrive(text)[0]) or text.startswith(("\\", "/"))))


def _start(text: str, here: Path) -> tuple[Path, list[str]]:
    """Where the text of a path or a link starts outside the root: its anchor, or ``here`` when it is relative."""
    path = Path(text)
    return (Path(os.path.realpath(path.anchor)), list(path.parts[1:])) if path.anchor else (here, list(path.parts))


def _inside(root: Path, text: str) -> str:
    """The rooted ``text`` as a path relative to ``root``, kept as text so that a link farther along the chain is
    still walked: by its own words when it lies under ``root``, else walked outside one link at a time (a checkout
    alias) up to the first name under ``root``, with the rest of the text left to :func:`_walk_links`."""
    lexical = Path(text)  # not normalised: a ``..`` after a link goes up from the link's target
    if lexical.is_relative_to(root):
        return lexical.relative_to(root).as_posix()
    here, pending = _start(text, root)
    hops = 0
    while True:
        if here.is_relative_to(root):  # also right after a restart at a link target with nothing left (the share root)
            return "/".join([here.relative_to(root).as_posix(), *pending])
        if not pending:
            break
        part = pending.pop(0)
        here = here.parent if part == ".." else here / part
        if here.is_relative_to(root) or not _is_link(here):
            continue
        hops += 1
        if hops > 40:
            raise _LinkLoop(f"too many levels of symbolic links: {text}")
        here, more = _start(_link_target(here), here.parent)
        pending = more + pending
    raise SourceArgumentError(f"path escapes the repository: {text}")


def _walk_links(root: Path, path: str) -> Path:
    """``path`` walked one component at a time, as a checkout does: a ``..`` after a link goes up from the link's
    target. :class:`SourceArgumentError` if a ``..`` has nothing left to go up from, an absolute text lands outside
    ``root``, or more than 40 links are followed."""
    if _rooted(path):
        path = _inside(root, path)
    pending = _parts(path)
    walked: list[str] = []
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == ".." or _rooted(part):  # ``sub/Q:x`` is joined as ``Q:x``, on drive Q
            if not walked or part != "..":
                raise SourceArgumentError(f"path escapes the repository: {path}")
            if not root.joinpath(*walked).is_dir():  # ENOENT / ENOTDIR in a checkout, as GitTree._follow says
                raise _NoSuchPath(f"no such path: {path}")
            walked.pop()
            continue
        walked.append(part)
        here = root.joinpath(*walked)
        if not _is_link(here):
            continue
        hops += 1
        if hops > 40:
            raise _LinkLoop(f"too many levels of symbolic links: {path}")
        text = _link_target(here)
        if _rooted(text):
            text = _inside(root, text)
            walked = []
        else:
            walked.pop()
        pending = _parts(text) + pending
    return root.joinpath(*walked)
