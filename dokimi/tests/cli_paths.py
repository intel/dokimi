# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The argv of the `novelty` commands, which #94 M2 moved under `skill` (`shorten` to `skill`)."""

from __future__ import annotations

SKILL_NOVELTY = ("analyze", "atoms", "probe", "items")


def novelty_argv(sub: str) -> list[str]:
    """`skill novelty <sub>`, `skill shorten`, or `novelty <sub>` for the commands that stayed."""
    if sub == "shorten":
        return ["skill", "shorten"]
    if sub in SKILL_NOVELTY:
        return ["skill", "novelty", sub]
    return ["novelty", sub]
