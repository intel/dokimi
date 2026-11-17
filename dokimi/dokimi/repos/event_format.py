# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The format version of ``events.jsonl`` (docs/events-format.md).

Each line carries its own ``v``: the major version of the format it was written under. A line with no
``v`` was written before the field existed and reads as version 1. There is no header line: an empty
``events.jsonl`` is what the runner writes for an agent that never started, and a header would make it
non-empty. Per-line versions also keep a line self-describing when a file is sliced or appended to.
"""

from __future__ import annotations

from typing import Any, Mapping

EVENT_VERSION = 1  # the major version this code writes; a version change adds repo_event.vN.json

#: The kinds the converters write in version 1. Readers must accept any other kind (a later minor addition).
KNOWN_KINDS = frozenset({"command", "read", "edit", "tool"})


def event_version(event: Mapping[str, Any]) -> int:
    """The format version of one event; an event with no ``v`` is the unversioned format, read as 1."""
    v = event.get("v")
    return v if isinstance(v, int) and not isinstance(v, bool) else 1
