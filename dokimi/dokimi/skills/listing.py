# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""How many characters of a skill's listing entry text the agent is shown.

``repo budget`` and the skill arm's receipt (``harnesses/skill_treatment.py``) both
count the entry with this one function, so they cannot disagree (#943).
"""

from typing import Any

WHEN_SEPARATOR = " - "


def listing_text_chars(description: str, when_to_use: Any) -> int:
    """Length of ``description`` plus ``" - "`` plus ``when_to_use``, before any cut.

    The separator is counted only for a non-empty ``when_to_use``; ``None`` and ``""`` add nothing.
    """
    when = "" if when_to_use is None else str(when_to_use)
    return len(description) + (len(WHEN_SEPARATOR) + len(when) if when else 0)


def listing_description(meta: dict[str, Any], body: str) -> str:
    """The description the listing shows: the frontmatter one, else the body's first non-empty line.

    Claude Code falls back to the body when ``description`` is omitted (#943).
    """
    return str(meta.get("description") or "") or next(
        (line.strip() for line in body.splitlines() if line.strip()), "")
