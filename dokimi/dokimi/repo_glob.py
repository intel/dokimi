# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Glob matching for repository suites and skill directories, walking the tree by segment: symbolic links are
followed as directories, and an unreadable directory either matches nothing or, in strict mode, is a ConfigError."""

from __future__ import annotations

import errno
import fnmatch
import os
from pathlib import Path


_MISSING = (errno.ENOENT, errno.ENOTDIR, errno.ELOOP)  # a path that is not there, or a link that loops: no entries


def _listing(base: Path, *, strict: bool = False) -> list[tuple[str, bool, bool]]:
    """(name, is_dir, is_symlink) of each entry of ``base`` that can be read. An unreadable one is left out, unless
    ``strict``: then its error is raised (a suite must not hash as if its files were absent). A missing ``base`` has
    no entries either way."""
    out: list[tuple[str, bool, bool]] = []
    try:
        with os.scandir(base) as it:
            for entry in it:
                try:
                    out.append((entry.name, entry.is_dir(), entry.is_symlink()))
                except OSError as exc:
                    if exc.errno == errno.ELOOP:  # a link that loops: still an entry, so a suite refuses it
                        out.append((entry.name, False, True))
                    elif strict and exc.errno not in _MISSING:
                        raise
    except OSError as exc:
        if strict and exc.errno not in _MISSING:
            raise
    return out


def _glob_walk(base: Path, segs: tuple[str, ...], i: int, found: set[Path], *, strict: bool,
               links: set[tuple[Path, tuple[str, ...]]]) -> None:
    """Match ``segs[i:]`` under ``base``, adding each match to ``found`` and each symbolic link a segment must read
    through to ``links``, with the segments left to match under the link's target (matched or not: a retargeted
    link may leave the pattern nothing to match)."""
    if i == len(segs):
        found.add(base)
        return
    seg = segs[i]
    more = i + 1 < len(segs)  # a link is read through only when a segment follows it
    if seg == "**":
        if more or not base.is_file():  # ** matches directories only: a file is not a zero-directory match
            _glob_walk(base, segs, i + 1, found, strict=strict, links=links)
        for name, is_dir, is_link in _listing(base, strict=strict):
            if is_link and more:
                links.add((base / name, segs[i + 1:]))
            if is_dir:  # follows symlinks: a linked directory is matched, but not descended
                _glob_walk(base / name, segs, i + 1 if is_link else i, found, strict=strict, links=links)
        return
    wild = any(c in seg for c in "*?[")  # matched by exact name against the listing: case-sensitive everywhere
    for name, _, is_link in _listing(base, strict=strict):
        if fnmatch.fnmatchcase(name, seg) if wild else name == seg:
            if is_link and more:
                links.add((base / name, segs[i + 1:]))
            _glob_walk(base / name, segs, i + 1, found, strict=strict, links=links)


def glob_links(root: Path, pattern: str) -> list[tuple[Path, tuple[str, ...]]]:
    """The symbolic links a walk of ``pattern`` reads through, matched or not, unreadable directories raising, each
    with the segments the pattern still has under its target (a link met by the last segment has none)."""
    links: set[tuple[Path, tuple[str, ...]]] = set()
    _glob_walk(root, tuple(pattern.split("/")), 0, set(), strict=True, links=links)
    return sorted(links)


def glob_paths(root: Path, pattern: str, *, strict: bool = False) -> list[Path]:
    """The paths ``pattern`` matches under ``root``. Every segment, literal ones included, is matched against the names
    in the directory listing, case-sensitively on every platform: ``Path.glob`` folds case on Windows, and 3.11 has no
    ``case_sensitive`` switch, so a suite would differ between OSes and from impact. ``**`` is a whole segment matching
    zero or more directories. It does not recurse through a symlinked directory: the link counts as one directory for the
    segments after it, so ``**/x`` reaches ``link/x`` but not ``link/sub/x``; a second ``**`` placed after a link can still
    descend it, so ``**/**/x`` is not guaranteed to match as ``**/x`` does. Unlike ``Path.glob`` on some versions, a
    linked directory is matched by ``**`` itself on every version. A directory that cannot be listed contributes no
    matches, unless ``strict``: then its error is raised."""
    found: set[Path] = set()
    _glob_walk(root, tuple(pattern.split("/")), 0, found, strict=strict, links=set())
    return sorted(found)
