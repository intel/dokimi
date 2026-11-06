# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``--keep-copies-under``: which copies of a same-name catalog skill stand for the name.

Split from ``novelty/routing.py``, which keeps the field rules under its growth cap. Nothing here
loads a skill; the caller hands it the listed copies.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dokimi.skills import Skill


def check_keep_copies_under(root: str, keep: str) -> None:
    """Refuse a ``--keep-copies-under`` value that is not a directory inside the catalog ``root``.

    The value is relative to ``root``. It may be the root itself, but not a path that leaves it
    (``../`` or a symlink out) and not a file.

    Raises:
        ValueError: ``keep`` is empty, absolute, resolves outside ``root``, or is not a directory.
    """
    if not keep:
        raise ValueError("--keep-copies-under is empty: name a directory under --catalog")
    if Path(keep).is_absolute():
        raise ValueError("--keep-copies-under is an absolute path: give a directory relative to "
                         f"--catalog: {keep}")
    base = Path(root).resolve()
    try:
        target = (base / keep).resolve()
    except RuntimeError as exc:  # a symlink loop on 3.11 and 3.12; 3.13 gives "not a directory" below
        raise ValueError(f"--keep-copies-under is a symlink loop: {keep}") from exc
    if not target.is_relative_to(base):
        raise ValueError(f"--keep-copies-under leaves --catalog: {keep} resolves outside {root}")
    if not target.is_dir():
        raise ValueError(f"--keep-copies-under is not a directory under --catalog: {keep}")


def under(path: Path, directory: Path) -> bool:
    """Whether ``path`` lies under ``directory``, both resolved."""
    return path.resolve().is_relative_to(directory)


def resolve_kept_copies(listed: list[tuple[Skill, str]], root: str, keep_copies_under: str | None,
                        notes: list[str] | None) -> list[tuple[Skill, str]]:
    """Keep only the copies under ``keep_copies_under`` for each name whose copies differ, when they agree.

    A name is resolved when the copies under the directory have one content between them. The
    others of that name are left out, with a note naming each. Names whose copies all share one
    content are untouched, and so is a name with no copy under the directory or with two contents
    under it: that one is left for :func:`routing._conflicting_names`. When no name is resolved,
    a note says so for each name whose copies still differ.
    """
    if keep_copies_under is None:
        return listed
    prefix = (Path(root) / keep_copies_under).resolve()
    by_name: dict[str, list[tuple[Skill, str]]] = {}
    for skill, digest in listed:
        by_name.setdefault(skill.name, []).append((skill, digest))
    resolved: set[str] = set()
    for name, copies in by_name.items():
        under_digests = {digest for skill, digest in copies if under(skill.path, prefix)}
        if len({digest for _, digest in copies}) > 1 and len(under_digests) == 1:
            resolved.add(name)
            if notes is not None:
                others = ", ".join(sorted(skill.path.as_posix() for skill, _ in copies
                                          if not under(skill.path, prefix)))
                notes.append(f"kept catalog skill {name!r} under {keep_copies_under}; left out its "
                             f"other copies: {others}")
    if not resolved and notes is not None:
        for name, copies in sorted(by_name.items()):
            if len({digest for _, digest in copies}) > 1:
                n = len({digest for skill, digest in copies if under(skill.path, prefix)})
                notes.append(f"--keep-copies-under {keep_copies_under} kept no copy: under it, "
                             f"{name!r} still has {n} contents" if n else
                             f"--keep-copies-under {keep_copies_under} kept no copy: no copy of "
                             f"{name!r} is under it")
    return [(skill, digest) for skill, digest in listed
            if skill.name not in resolved or under(skill.path, prefix)]
