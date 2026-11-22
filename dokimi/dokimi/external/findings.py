# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The records the skill checks return: one :class:`Finding`, and the :class:`SkillReview` of one skill.

Kept out of :mod:`dokimi.external.validate` so the format rules (:mod:`dokimi.external.skill_format`) can
build findings without importing the checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional


@dataclass
class Finding:
    """One check that did not pass: ``level`` is ``note``, ``warn`` or ``error``.

    ``script`` is the bundled script a script finding is about, relative to the skill directory (#961); None otherwise.
    """

    level: str
    code: str
    message: str
    script: Optional[str] = None


def state_of(findings: Iterable[Finding]) -> str:
    """``error`` if any finding is an error, else ``warn`` if any is a warning, else ``ok``."""
    levels = {f.level for f in findings}
    return "error" if "error" in levels else "warn" if "warn" in levels else "ok"

@dataclass
class SkillReview:
    """What :func:`review_skills` found for one loaded skill."""

    findings: list[Finding] = field(default_factory=list)
    disable_model_invocation: bool = False
    content_sha256: Optional[str] = None
    duplicate_of: Optional[str] = (
        None  # the skill directory of the earlier copy, relative to the catalog root
    )
    listing_chars: Optional[int] = None  # the skill's entry in a skill listing (listing.entry_chars); None when unread

    @property
    def state(self) -> str:
        """``error`` if any finding is an error, else ``warn`` if any is a warning, else ``ok``."""
        return state_of(self.findings)
