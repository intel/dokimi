# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Review the skills of an external catalog: spec frontmatter, and identical copies.

Runs after the readers of :mod:`dokimi.external.catalog` and only on skills
that loaded (a load error is already reported as an error by the scan). Checks
follow the Agent Skills spec:

- ``name`` is the directory name, kebab-case (lowercase letters, digits and
  single hyphens) and at most 64 characters. A mismatch or a bad format is an
  ``error``; a ``name`` missing from the frontmatter is a ``warn``, because the
  loader falls back to the directory name silently.
- ``description`` is at most 1024 characters (``error``). A missing or empty
  description is a load error, raised by the loader.

Identical copies: a ``SKILL.md`` whose bytes are the same as an earlier
skill's is a ``warn`` naming that earlier skill. The model sees only the name and
description, so two such skills compete for the same query even when their bundled
files differ (a plugin copy with an extra ``agents/`` folder is still a mirror).
Mirrors are common (one skill checked in under two plugin directories), and
counting them twice inflates routing and coverage numbers.

``disable-model-invocation: true`` is not a finding: the model is never offered
that skill, so it is reported as a flag (the same reading as the skill arm's
check in :mod:`dokimi.harnesses.skill_treatment`).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from dokimi.skills import Skill
from dokimi.utils import parse_frontmatter

from .catalog import SkillCatalogEntry

NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
DESCRIPTION_MAX = 1024


@dataclass
class Finding:
    """One check that did not pass: ``level`` is ``warn`` or ``error``."""

    level: str
    code: str
    message: str


@dataclass
class SkillReview:
    """What :func:`review_skills` found for one loaded skill."""

    findings: list[Finding] = field(default_factory=list)
    disable_model_invocation: bool = False
    content_sha256: Optional[str] = None
    duplicate_of: Optional[str] = None  # the skill directory of the earlier copy, relative to the catalog root

    @property
    def state(self) -> str:
        """``error`` if any finding is an error, else ``warn`` if any is a warning, else ``ok``."""
        levels = {f.level for f in self.findings}
        return "error" if "error" in levels else "warn" if "warn" in levels else "ok"


def _frontmatter_findings(skill: Skill) -> tuple[list[Finding], bool]:
    """The spec checks on the frontmatter of ``skill``, and its ``disable-model-invocation`` flag."""
    meta, _ = parse_frontmatter(skill.path.read_text(encoding="utf-8"))
    dir_name = skill.path.parent.name
    findings: list[Finding] = []
    declared = str(meta.get("name") or "").strip()
    if not declared:
        findings.append(Finding("warn", "name-missing",
                                f"no name in the frontmatter; the directory name {dir_name!r} is used"))
    else:
        if declared != dir_name:
            findings.append(Finding("error", "name-mismatch",
                                    f"name {declared!r} is not the directory name {dir_name!r}"))
        if len(declared) > NAME_MAX or not NAME_PATTERN.fullmatch(declared):
            findings.append(Finding("error", "name-format",
                                    f"name {declared!r} is not kebab-case (lowercase letters, digits, "
                                    f"single hyphens; at most {NAME_MAX} characters)"))
    if len(skill.description) > DESCRIPTION_MAX:
        findings.append(Finding("error", "description-too-long",
                                f"description is {len(skill.description)} characters; the limit is {DESCRIPTION_MAX}"))
    # Read as the skill arm reads it (dokimi.harnesses.skill_treatment.check_skill).
    hidden = str(meta.get("disable-model-invocation", "")).strip().lower() in ("1", "true", "yes", "on")
    return findings, hidden


def _skill_digest(skill: Skill) -> str:
    """SHA-256 of the ``SKILL.md`` bytes: the text the model is shown when it chooses a skill."""
    return hashlib.sha256(skill.path.read_bytes()).hexdigest()


def review_skills(entries: Sequence[SkillCatalogEntry]) -> list[Optional[SkillReview]]:
    """One :class:`SkillReview` per entry, in order; None for an entry that did not load.

    The first skill of each identical ``SKILL.md`` is the original; a later copy names it in
    ``duplicate_of``.
    """
    reviews: list[Optional[SkillReview]] = []
    first_by_digest: dict[str, Skill] = {}
    for entry in entries:
        if not entry.loaded or entry.skill is None:
            reviews.append(None)
            continue
        skill = entry.skill
        findings, hidden = _frontmatter_findings(skill)
        review = SkillReview(findings=findings, disable_model_invocation=hidden)
        review.content_sha256 = _skill_digest(skill)
        original = first_by_digest.setdefault(review.content_sha256, skill)
        if original is not skill:
            where = original.path.parent.relative_to(entry.source_root).as_posix()
            review.duplicate_of = where
            review.findings.append(Finding("warn", "duplicate",
                                           f"SKILL.md is identical to {where}; the same skill is counted twice"))
        reviews.append(review)
    return reviews
