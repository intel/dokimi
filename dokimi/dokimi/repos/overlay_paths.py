# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Link-aware path checks for the ``guidance:overlay`` arm (:mod:`.workspace`).

The arm removes the overlays of other agents from a workspace. Before it does, it finds each one by its canonical
path (a name under a directory link is the file it reaches), and it checks whether a path of the base runs through
one of them, following symbolic links hop by hop as the workspace's own paths would be followed.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import PrepError


def _canon(path: Path) -> Path:
    """``path`` with its directories resolved and its final name kept, so a link is compared by its own name."""
    return Path(os.path.realpath(path.parent)) / path.name


_MAX_HOPS = 64  # symbolic links followed by one walk; a cycle runs out


def _link_target(link: Path) -> Path:
    """The target of a symbolic link as a path. On Windows ``os.readlink`` gives an absolute target with the ``\\\\?\\``
    long-path prefix, which the canonical paths of the workspace do not carry, so the prefix is dropped; a UNC share
    keeps its server name as ``\\\\server\\share`` (``\\\\?\\UNC\\server\\share`` is that share, not a relative path)."""
    target = os.readlink(link)
    if target.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + target[len("\\\\?\\UNC\\"):])
    return Path(target[len("\\\\?\\"):] if target.startswith("\\\\?\\") else target)


def _path_hits(ws: Path, rel: str, gone: set[Path]) -> bool:
    """Whether the workspace path ``rel`` runs through one of the canonical paths ``gone``, a link or a plain name.
    Walked from the workspace root: each name is checked before it is resolved, so a removed directory link named in
    the middle of the path is a hit (the path would not exist once it is removed); a link is followed hop by hop,
    and a cycle ends the walk."""
    resolved = Path(os.path.realpath(ws))
    todo = rel.split("/")
    hops = 0
    while todo:
        part = todo.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            resolved = resolved.parent
            continue
        cand = resolved / part
        if cand in gone:
            return True
        if not cand.is_symlink():
            resolved = cand
            continue
        hops += 1
        if hops > _MAX_HOPS:
            return False
        target = _link_target(cand)  # the native separators: a Windows target splits on backslashes
        if target.is_absolute():
            resolved, parts = Path(target.anchor), target.parts[1:]
        else:
            parts = target.parts
        todo[:0] = list(parts)
    return False


def _overlay_targets(ws: Path, listed: list[str], arm: str = "guidance:overlay") -> dict[Path, str]:
    """The overlays of other agents to remove, by canonical path (the directories resolved, so a name under a link
    is the file it reaches), one name per file: a directory the index lists that a setup patch made a link to another
    directory names the same file twice, and the first name in order stands for it."""
    names: dict[Path, str] = {}
    for rel in listed:  # a setup patch may have made a directory of the index a link out of the workspace
        if not _canon(ws / rel).parent.is_relative_to(ws.resolve()):
            raise PrepError(f"{arm}: {rel} is under a path that resolves outside the workspace")
        names.setdefault(_canon(ws / rel), rel)
    return names


def _supplies_a_file(ws: Path, rel: str) -> bool:
    """Whether the workspace path ``rel`` resolves to a regular file inside the workspace."""
    real = Path(os.path.realpath(ws / rel))
    return real.is_file() and real.is_relative_to(ws.resolve())
