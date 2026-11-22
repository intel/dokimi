# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The argv of the `novelty` commands, which #94 M2 moved under `skill` (`shorten` to `skill`; `view` in M3)."""

from __future__ import annotations

def novelty_argv(sub: str) -> list[str]:
    """`skill novelty <sub>`, `skill shorten` or `skill routing` for the commands that moved."""
    if sub == "shorten":
        return ["skill", "shorten"]
    if sub == "routing":
        return ["skill", "routing"]
    return ["skill", "novelty", sub]


def artifact_flag(sub: str, ref: str) -> list[str]:
    """The skill argument of `novelty_argv(sub)`: `--artifact REF`, or REF itself for `skill routing`."""
    return [ref] if sub == "routing" else ["--artifact", ref]
