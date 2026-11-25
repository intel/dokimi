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
#: The token-accounting kind a converter writes, not named in ``repo_event.v2.json``: naming it there adds an
#: ``allOf`` entry, which ``schema_compat`` reads as breaking.
ACCOUNTING_KINDS = frozenset({"usage_remainder"})
#: The tool and turn kinds the converters write in version 2 (version 1 wrote the tool kinds only).
#: ``ACCOUNTING_KINDS`` adds the remainder; readers accept any other kind.
KNOWN_KINDS = TOOL_KINDS | TURN_KINDS


def plain_path(path: str) -> str:
    """``path`` with its ``.`` components and repeated ``/`` dropped; a ``..`` stays, as the name before it may be a
    link, which only the workspace can tell (:class:`~dokimi.repos.held_guidance.HeldGuidance`). An empty path is ``.``."""
    lead = "/" if path.startswith("/") else ""
    return lead + "/".join(p for p in path.split("/") if p not in ("", ".")) or "."


def event_version(event: Mapping[str, Any]) -> int:
    """The format version of one event; an event with no ``v`` is the unversioned format, read as 1."""
    v = event.get("v")
    return v if isinstance(v, int) and not isinstance(v, bool) else 1
