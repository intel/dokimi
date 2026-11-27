# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The link and path checks on the body of a ``SKILL.md`` (``catalog check``): missing targets, or targets outside it."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional

from dokimi.skills import Skill

from .bundle_paths import path_missing
from .finding_lines import path_needles, text_line
from .findings import Finding
from .link_files import _file_ref
from .paths import _exists, _in_checkout, _inside, _resolved
from .skill_markdown import link_pieces, outside_fences


def _link_finding(raw: str, skill_dir: Path, source_root: Path, real_dir: Path) -> Optional[Finding]:
    """The finding for one link destination (inline or reference), or None when it is fine."""
    text = _file_ref(raw, link=True)
    if text is None:
        return None
    named = skill_dir / text
    if not _inside(named, skill_dir):
        return _escapes_link(raw)
    target = _resolved(named)
    if target is not None and not target.is_relative_to(real_dir):
        return _escapes_link(raw)
    if target is not None and _exists(target):
        return None
    from_root = ""
    rooted = _in_checkout(source_root / text, source_root)
    if rooted is not None and _exists(rooted):
        from_root = " (it exists from the catalog root: the base is wrong)"
    return Finding("warn", "link-missing",
                   f"link {raw!r} names no file relative to the skill directory{from_root}; "
                   "fix the link or add the file")


def _span_finding(raw: str, skill_dir: Path, source_root: Path, real_dir: Path) -> Optional[Finding]:
    """The finding for a code span that names an existing file outside the skill (``../``), or None."""
    text = _file_ref(raw)
    if text is None or not raw.startswith("../"):
        return None
    target = _in_checkout(skill_dir / text, source_root)
    if target is None or not _exists(target) or target.is_relative_to(real_dir):
        return None  # outside the checkout, as written or resolved: not read
    return Finding(
        "warn",
        "path-escapes-skill",
        f"{raw!r} points outside the skill directory; it breaks when the skill "
        "is installed alone",
    )


def bundle_findings(skill: Skill, source_root: Path) -> list[Finding]:
    """Link and path checks on the body of ``skill``: missing targets, or targets outside it.

    A target is classed by its written name, then by where it resolves, before any file is read:
    one outside the skill directory is ``link-escapes-skill`` whether or not it exists, and one
    outside the catalog checkout (through a symlink too) is never stat-ed.
    """
    skill_dir = skill.path.parent
    real_dir = skill_dir.resolve()
    targets, spans = link_pieces(outside_fences(skill.body))
    found = [_link_finding(raw, skill_dir, source_root, real_dir) for raw in targets]
    found += [_span_finding(raw, skill_dir, source_root, real_dir) for raw in spans]
    linked = frozenset(text.removeprefix("./") for text in (_file_ref(raw, link=True) for raw in targets) if text)
    text = skill.path.read_text(encoding="utf-8")
    # The line is the first line of SKILL.md that holds the written link or span (its quoted form for a path).
    needles = [((f"({raw})", f"({raw} ", f"]: {raw}"), f"({raw}", raw) for raw in targets] + [(f"`{raw}`", raw) for raw in spans]
    located = [replace(f, line=text_line(text, *needle)) for f, needle in zip(found, needles, strict=True) if f is not None]
    return located + [
        Finding("warn", "path-missing", message, line=text_line(text, *path_needles(message)))
        for message in path_missing(spans, skill_dir, source_root, real_dir, linked)
    ]



def _escapes_link(raw: str) -> Finding:
    return Finding(
        "warn",
        "link-escapes-skill",
        f"link {raw!r} points outside the skill directory; it breaks when the skill is installed alone",
    )
