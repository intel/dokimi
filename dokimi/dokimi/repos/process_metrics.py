# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Process metrics of one run, from its ``events.jsonl`` events, for ``result.json`` ``process`` (contract §4.1).

The framework counts what every repository asks of a trace:

- ``tool_calls``: every event, so every tool call of the parent and its subagents;
- ``commands``: the ``command`` events;
- ``failed_commands``: commands whose ``rc`` is neither ``0`` nor ``null`` (``null`` means no
  result came back, which is not a failure);
- ``denied_commands``: commands a hook or the harness's permissions refused (``denied``);
- ``guidance_read``: the sorted paths of ``read`` events matched by the manifest's guidance globs.

``counters`` holds the manifest's ``process_metrics``: for each name, how many ``command`` events
its regex finds a match in (``re.search``). A counter matches the command text unless it names
``output`` (the command's output, as clipped in the event) or ``any`` (either).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

COUNTER_FIELDS = ("text", "output", "any")


@dataclass(frozen=True)
class Counter:
    """One ``process_metrics`` entry: a compiled regex and the command field it searches."""

    pattern: re.Pattern[str]
    field: str = "text"

    def hits(self, event: Mapping[str, Any]) -> bool:
        fields = ("text", "output") if self.field == "any" else (self.field,)
        return any(self.pattern.search(str(event.get(f) or "")) for f in fields)


def parse_counter(name: str, value: Any) -> Counter:
    """A ``process_metrics`` value, ``<regex>`` or ``{regex: <regex>, match: text|output|any}``.

    Raises ``ValueError`` naming the metric when the regex does not compile or the shape is wrong."""
    regex, where = (value, "text") if isinstance(value, str) else (
        (value.get("regex"), value.get("match", "text")) if isinstance(value, dict) else (None, None))
    if not isinstance(regex, str) or not regex:
        raise ValueError(f"process_metrics/{name}: give a regex, or {{regex: <regex>, match: text|output|any}}")
    if where not in COUNTER_FIELDS:
        raise ValueError(f"process_metrics/{name}: match must be one of {', '.join(COUNTER_FIELDS)}, got {where!r}")
    try:
        return Counter(re.compile(regex), where)
    except re.error as exc:
        raise ValueError(f"process_metrics/{name}: not a valid regex: {exc}") from exc


def process_metrics(events: Iterable[Mapping[str, Any]], is_guidance: Callable[[str], bool],
                    counters: Mapping[str, Counter] | None = None) -> dict[str, Any]:
    """The ``process`` object of ``result.json`` for one run's events; ``is_guidance`` tells a guidance path."""
    events = list(events)
    commands = [e for e in events if e.get("kind") == "command"]
    reads = {str(e["path"]) for e in events if e.get("kind") == "read" and e.get("path")}
    return {
        "tool_calls": len(events),
        "commands": len(commands),
        "failed_commands": sum(e.get("rc") not in (0, None) for e in commands),
        "denied_commands": sum(bool(e.get("denied")) for e in commands),
        "guidance_read": sorted(p for p in reads if is_guidance(p)),
        "counters": {name: sum(c.hits(e) for e in commands) for name, c in (counters or {}).items()},
    }
