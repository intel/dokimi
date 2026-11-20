# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which reviewed skills a harness lists, and so the routing field and ``catalog check`` agree on."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from dokimi.utils import parse_frontmatter

if TYPE_CHECKING:
    from dokimi.skills.loader import Skill

    from .validate import SkillReview

ENTRY_SEP = ": "  # between a skill's name and its description in a listing entry
WHEN_SEP = " - "  # between the description and ``when_to_use``, as the harness listing joins them


def entry_chars(skill: Skill) -> int:
    """The characters of the skill's listing entry: ``name: description``, then `` - when_to_use`` when set (#966).

    Read from the ``SKILL.md`` frontmatter, so the whole-catalog scan, ``--per-skill``, ``--jobs`` workers and the
    row a ``--resume`` keeps all count one skill the same way. A line break between entries is not counted.
    """
    meta, _ = parse_frontmatter(skill.path.read_text(encoding="utf-8"))
    text = f"{skill.name}{ENTRY_SEP}{skill.description}"
    if meta.get("when_to_use") is not None:
        text += f"{WHEN_SEP}{meta['when_to_use']}"
    return len(text)


def listed_chars(review: Optional[SkillReview]) -> Optional[int]:
    """The listing characters a skill adds to the routing field: None when it is not listed (``in_field``)."""
    return review.listing_chars if review is not None and in_field(review) else None


def only_description_too_long(review: SkillReview) -> bool:
    """Whether every spec error of ``review`` is a description over 1024 characters.

    Harnesses differ on such a skill: pi.dev rejects it, Claude Code lists it and truncates the
    description past about 1,536 characters. The routing field keeps it, so a result is not
    silently measured without a skill that some harness would list. Any other spec error still
    leaves a skill out.
    """
    errors = [f for f in review.findings if f.level == "error"]
    return bool(errors) and all(f.code == "description-too-long" for f in errors)


def in_field(review: SkillReview) -> bool:
    """Whether the routing field lists the skill by default: not disabled, no spec error but length."""
    return not review.disable_model_invocation and (
        review.state != "error" or only_description_too_long(review))
