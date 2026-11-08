# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Review one skill directory alone, and find the skills nested in another skill's bundle.

:func:`review_skill_dir` is the per-skill entry point for a corpus run: it reviews one
skill directory with no other skill in view, so no cross-catalog check can fire.
:func:`nested_in_bundles` marks a ``SKILL.md`` that sits inside another skill's directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

from .catalog import SkillCatalogEntry, _scan_skill_files
from .validate import Finding, SkillReview, review_skills


def _same_dir(a: Path, b: Path) -> bool:
    return a.resolve() == b.resolve()


def review_skill_dir(path: Path | str) -> SkillReview:
    """The review of the skill in directory *path*, with no other skill in view.

    Only *path* and the files below it are read; a ``SKILL.md`` nested under *path* is a
    different skill and is not reviewed here. The checkout root is taken to be *path*, so the
    directory name is not compared with the frontmatter ``name``, and a link that leaves
    *path* is ``link-escapes-skill``, as when the skill is installed alone. Every SKILL.md the scan finds
    below *path* is loaded to find its own, and the other bodies are discarded (eager; lazy loading is #434).

    A skill that does not load gets one ``error`` finding (``not-loaded``) with the loader's
    message. Raises ``ValueError`` when *path* is not a directory or holds no ``SKILL.md``.
    """
    skill_dir = Path(path)
    if not skill_dir.is_dir():
        raise ValueError(f"not a skill directory: {skill_dir}")
    own = [e for e in _scan_skill_files(skill_dir) if e.path is not None and _same_dir(e.path, skill_dir)]
    if not own:
        raise ValueError(f"no SKILL.md in {skill_dir}")
    review = review_skills(own[:1], standalone=True)[0]
    if review is None:
        return SkillReview(findings=[Finding("error", "not-loaded", own[0].error or "the skill did not load")])
    return review


def entry_relpath(entry: SkillCatalogEntry) -> Optional[str]:
    """The skill directory relative to its catalog root, as posix; None when the entry has no path."""
    if entry.path is None:
        return None
    try:
        return entry.path.relative_to(entry.source_root).as_posix()
    except ValueError:
        return entry.path.as_posix()


def nested_in_bundles(entries: Sequence[SkillCatalogEntry], root: Path) -> list[Optional[str]]:
    """For each entry, the relative path of the skill whose directory holds it, else None.

    An entry is nested when one of its ancestor directories below *root* is the directory of
    another entry, such as a sub-skill kept inside a skill's bundle. A ``SKILL.md`` directly in
    *root* is the checkout, not a bundle, so it encloses nothing.
    """
    root_r = Path(root).resolve()
    bundles: dict[Path, Optional[str]] = {}
    for entry in entries:
        if entry.path is not None and entry.path.resolve() != root_r:
            # The label is the row's relpath, which is not resolved: a symlinked catalog must still match it.
            bundles.setdefault(entry.path.resolve(), entry_relpath(entry))
    return [
        None if entry.path is None else _enclosing(entry.path.resolve(), root_r, bundles)
        for entry in entries
    ]


def _enclosing(path_r: Path, root_r: Path, bundles: dict[Path, Optional[str]]) -> Optional[str]:
    for ancestor in path_r.parents:
        if not ancestor.is_relative_to(root_r) or ancestor == root_r:
            return None
        if ancestor in bundles:
            return bundles[ancestor]
    return None
