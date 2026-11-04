# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Per-agent overlays of AGENTS.md (agentsmd/agents.md#185): ``AGENTS.<agent>.md`` beside an ``AGENTS.md``.

An overlay is named by the agent it is for, in lower case (``AGENTS.claude.md``), and the name must be the
``overlay`` key of a builtin harness profile: ``AGENTS.ja.md`` or ``AGENTS.local.md`` is an ordinary file. The
``guidance:overlay`` arm keeps the overlay of that name and removes the others, and the reach probe reports any
foreign overlay a harness loaded anyway.
"""

from __future__ import annotations

import functools
import re
from pathlib import PurePosixPath

from .harness_profile import PROFILE_DIR, builtin_profiles, load_profile

OVERLAY_RE = re.compile(r"AGENTS\.([A-Za-z0-9][A-Za-z0-9-]*)\.md")


@functools.lru_cache(maxsize=1)
def native_names() -> frozenset[str]:
    """Guidance names a builtin harness loads by its own convention (``AGENTS.override.md``): never an overlay."""
    return frozenset(
        n
        for name in builtin_profiles()
        for n in load_profile(str(PROFILE_DIR / f"{name}.yaml")).guidance_names
    )


@functools.lru_cache(maxsize=1)
def overlay_names() -> frozenset[str]:
    """The agent names a builtin harness profile has an overlay for (its ``overlay`` key): the only names a file
    ``AGENTS.<name>.md`` is read as an overlay for. A profile loaded from outside the builtin set adds none."""
    names = set()
    for name in builtin_profiles():
        overlay = load_profile(str(PROFILE_DIR / f"{name}.yaml")).overlay
        if overlay is not None:
            names.add(overlay)
    return frozenset(names)


def overlay_of(path: str) -> str | None:
    """The agent the guidance file ``path`` is an overlay for, ``None`` when it is not one.

    A file under ``.claude`` is skill content, not guidance, so it is never an overlay. A name that no builtin
    profile has an overlay for (``AGENTS.ja.md``) is an ordinary file, not an overlay.
    """
    parts = PurePosixPath(path).parts
    if ".claude" in parts or parts[-1] in native_names():
        return None
    match = OVERLAY_RE.fullmatch(parts[-1])
    name = match.group(1).lower() if match else None
    return name if name in overlay_names() else None
