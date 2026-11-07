# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Which reviewed skills a harness lists, and so the routing field and ``catalog check`` agree on."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .validate import SkillReview


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
