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
    ``path`` (#942) is the file the finding is about, relative to the skill directory, set only when it is not the
    ``SKILL.md``; ``line`` (#942) is a 1-based line of that file (of ``SKILL.md`` when ``path`` is None). Both are
    None when the check does not know the position.
    """

    level: str
    code: str
    message: str
    script: Optional[str] = None
    line: Optional[int] = None
    path: Optional[str] = None


def locate(f: Finding, *, path: bool = True) -> dict:
    """The optional ``script``, ``line`` and ``path`` keys of a finding row: each only when the finding sets it (#942).

    ``path=False`` leaves ``path`` out, for a row whose own ``path`` already names the skill directory.
    """
    keys = (("script", f.script), ("line", f.line)) + ((("path", f.path),) if path else ())
    return {key: value for key, value in keys if value}


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
