# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Read an artifact reference into its text, the first step of every novelty stage.

Kept apart from :mod:`dokimi.novelty.analyze` so the bundle loader can read the entry
point without importing the analyzer that imports it.
:func:`load_artifact_text` stays importable from ``dokimi.novelty.analyze``.
"""

from __future__ import annotations

from pathlib import Path


def load_artifact_text(ref: str) -> tuple[str, str, str, str, str]:
    """Resolve an artifact reference to ``(kind, name, description, frontmatter, body)``.

    Supported today: ``skill:<path>`` (a ``SKILL.md`` or its directory) and
    ``text:<path>`` for any other context artifact. A plain path is treated as text, and
    a text artifact has no description — which means its need graph, and therefore its
    relevance weighting, is weaker. The analyzer says so in a warning rather than
    pretending the two cases are equivalent.
    """
    if ref.startswith("skill:"):
        from dokimi.skills.loader import load_skill

        skill = load_skill(ref.removeprefix("skill:"))
        # Verbatim, as shorten.render promises: a rebuilt `description: a: b` is not YAML.
        lines = skill.path.read_text(encoding="utf-8").lstrip("﻿").splitlines()
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        frontmatter = "\n".join(lines[: end + 1])
        return "skill", skill.name, skill.description, frontmatter, skill.body
    path = Path(ref.removeprefix("text:"))
    if not path.is_file():
        raise FileNotFoundError(f"artifact not found: {path}")
    return "text", path.stem, "", "", path.read_text(encoding="utf-8")
