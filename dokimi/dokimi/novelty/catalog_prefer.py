# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``--prefer-path``: which copy of a same-name catalog skill stands for the name.

Split from ``novelty/routing.py``, which keeps the field rules under its growth cap. Nothing here
loads a skill; the caller hands it the listed copies.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dokimi.skills import Skill


def under(path: Path, directory: Path) -> bool:
    """Whether ``path`` lies under ``directory``, both resolved."""
    return path.resolve().is_relative_to(directory)


def resolve_by_prefer(listed: list[tuple[Skill, str]], root: str, prefer_path: str | None,
                      notes: list[str] | None) -> list[tuple[Skill, str]]:
    """Keep the copies under ``prefer_path`` for each name whose copies differ, when they agree.

    A name is resolved when the copies under ``prefer_path`` have one content between them. The
    others of that name are left out, with a note naming each. A name with no copy under the path,
    or with two contents under it, is left for :func:`_conflicting_names`.
    """
    if prefer_path is None:
        return listed
    prefix = (Path(root) / prefer_path).resolve()
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
                notes.append(f"kept catalog skill {name!r} under {prefer_path}; left out its "
                             f"other copies: {others}")
    return [(skill, digest) for skill, digest in listed
            if skill.name not in resolved or under(skill.path, prefix)]

