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

EVENT_VERSION = 2  # the major version this code writes; a version change adds repo_event.vN.json

#: The kinds that are tool activity: what ``tool_calls`` counts.
TOOL_KINDS = frozenset({"command", "read", "edit", "tool"})
#: The per-model-call kinds (:mod:`dokimi.repos.turn_events`): not tool calls.
TURN_KINDS = frozenset({"turn", "compaction"})
#: The kinds the converters write in version 2 (version 1 wrote the tool kinds only). Readers accept any other kind.
KNOWN_KINDS = TOOL_KINDS | TURN_KINDS


def event_version(event: Mapping[str, Any]) -> int:
    """The format version of one event; an event with no ``v`` is the unversioned format, read as 1."""
    v = event.get("v")
    return v if isinstance(v, int) and not isinstance(v, bool) else 1
