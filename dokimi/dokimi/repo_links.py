# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Symlink-aware reads of a repository for dokimi_config: where a path resolves, the links on its declared way and on
the way to a target, and the entries under a directory. Paths are repository-relative segment tuples where they are
returned, so they match the impact a changed file maps to."""

from __future__ import annotations

import errno
import os
from pathlib import Path

from dokimi.utils import _plain_path


def _real(path: Path) -> Path | None:
    """The resolved path, or None when it cannot be resolved: a symlink loop (3.11 and 3.12 raise RuntimeError for it;
    3.13 and later resolve it to itself, so the loop is told apart by stat, which fails with ELOOP)."""
    try:
        real = path.resolve()
    except (OSError, RuntimeError):
        return None
    try:
        path.stat()
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return None
    return real


def _walk(path: Path, rel: str, *, dirs: bool = False) -> list[tuple[str, Path]]:
    """Entries under ``path`` (or ``path`` itself): regular files and symlinks, and with ``dirs`` the directories
    too, so a deleted file under a directory that still exists maps to it. Symlinks are followed into their
    directories; a directory is visited once by its resolved path, so a cycle ends."""
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()

    def visit(entry: Path, name: str) -> None:
        if entry.is_symlink():
            found.append((name, entry))
        if entry.is_dir():
            if dirs:
                found.append((name, entry))
            real = _real(entry)
            if real is None or real in seen:
                return
            seen.add(real)
            for child in sorted(entry.iterdir(), key=lambda p: p.name):
                visit(child, f"{name}/{child.name}")
        elif entry.is_file() and not entry.is_symlink():
            found.append((name, entry))

    visit(path, rel)
    return sorted(found)


def _within(rel: str, base: str) -> bool:
    """True when ``rel`` is ``base`` or sits under it (repository-relative paths)."""
    return rel == base or rel.startswith(base + "/")


def _under(segs: tuple[str, ...], base: tuple[str, ...]) -> bool:
    return segs[:len(base)] == base


def _inside(path: Path, root_real: Path) -> tuple[str, ...] | None:
    """The segments of ``path``'s resolved location under the repository root; None when it is the root or outside it."""
    real = _real(path)
    if real is None or real == root_real or not real.is_relative_to(root_real):
        return None
    return tuple(real.relative_to(root_real).as_posix().split("/"))


def _declared_links(path: Path, root: Path) -> list[tuple[str, ...]]:
    """The links on the declared way to ``path`` (a path under ``root``, as written), the path itself included when it is
    one: the components that are symbolic links, not the links met by following them."""
    parts = path.relative_to(root).parts
    return [parts[:k] for k in range(1, len(parts) + 1) if root.joinpath(*parts[:k]).is_symlink()]


def _hops(path: Path, root: Path, root_real: Path) -> list[tuple[str, ...]]:
    """The repository-relative links met resolving ``path`` (a path under ``root``, as written): each link on its
    declared way, and each link on the way to every target a link points to, until the path is resolved. A link's
    location is its real one, as git names it. A loop is cut after a fixed number of hops. The walk is kept at its real
    place in the filesystem, so a ``..`` may leave the repository and come back into it: a link met on the way back is
    still recorded, and a link outside the repository is followed but not returned."""
    pending = list(path.relative_to(root).parts)
    here = root_real  # the real directory the next component is looked up in
    found: dict[tuple[str, ...], None] = {}
    hops = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            here = here.parent  # the directory holding ``here`` is real, so its parent is the kernel's parent
            continue
        link = here / part
        if not link.is_symlink():
            here = link
            continue
        hops += 1
        if hops > 256:
            break
        if link.is_relative_to(root_real):
            found[tuple(link.relative_to(root_real).parts)] = None
        target = _plain_path(os.readlink(link))  # Windows reads an absolute target back as \\?\C:\...
        if os.path.isabs(target):
            anchor, *names = Path(target).parts  # walked from the filesystem root, as the kernel walks it
            pending = [*names, *pending]
            here = Path(anchor)
        else:
            pending = [*Path(target).parts, *pending]  # a relative target is read from the link's directory: here
    return list(found)
