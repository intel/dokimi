# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Process metrics of one run, from its ``events.jsonl`` events, for ``result.json`` ``process`` (contract §4.1).

The framework counts what every repository asks of a trace:

- ``tool_calls``: every event, so every tool call of the parent and its subagents;
- ``commands``: the ``command`` events;
- ``failed_commands``: commands not denied whose ``rc`` is neither ``0`` nor ``null`` (``null`` means
  no result came back, which is not a failure; a permission denial carries ``rc`` 1 but never ran);
- ``denied_commands``: commands a hook or the harness's permissions refused (``denied``);
- ``guidance_read``: the sorted workspace paths of ``read`` events (the Read tool only; a Bash ``cat`` is not
  counted, a ``process_metrics`` counter can) not denied that the manifest's guidance globs match; a path
  outside the workspace (absolute, or with ``..``) is no repository file.

``counters`` holds the manifest's ``process_metrics``: for each name, how many ``command`` events
its regex finds a match in (``re.search``). A counter matches the command text unless it names
``output`` (the command's output, as clipped in the event) or ``any`` (either). A refused command
(``denied``) is searched in its text only: its output is the hook's refusal message.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

COUNTER_FIELDS = ("text", "output", "any")


@dataclass(frozen=True)
class Counter:
    """One ``process_metrics`` entry: a compiled regex and the command field it searches."""

    pattern: re.Pattern[str]
    field: str = "text"

    def hits(self, event: Mapping[str, Any]) -> bool:
        fields: tuple[str, ...] = ("text", "output") if self.field == "any" else (self.field,)
        if event.get("denied"):  # a refused command's output is the hook's refusal message, not the command's
            fields = tuple(f for f in fields if f != "output")
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


def _workspace_path(path: str) -> str | None:
    """``path`` normalized (``./a``, ``a//b``, ``a/./b``), or None when it is not inside the workspace.

    A ``..`` that stays inside (``src/../AGENTS.md``) is resolved; ``~`` is a home directory, not the repository."""
    if path.startswith("~"):
        return None
    p = PurePosixPath(posixpath.normpath(path))
    if p.is_absolute() or ".." in p.parts:
        return None
    return str(p)


def process_metrics(events: Iterable[Mapping[str, Any]], is_guidance: Callable[[str], bool],
                    counters: Mapping[str, Counter] | None = None) -> dict[str, Any]:
    """The ``process`` object of ``result.json`` for one run's events; ``is_guidance`` tells a guidance path."""
    events = list(events)
    commands = [e for e in events if e.get("kind") == "command"]
    # A read that failed (rc 1: the file does not exist) read no guidance.
    reads = {w for e in events if e.get("kind") == "read" and e.get("path") and not e.get("denied")
             and e.get("rc") in (0, None) and (w := _workspace_path(str(e["path"]))) is not None}
    return {
        "tool_calls": len(events),
        "commands": len(commands),
        "failed_commands": sum(e.get("rc") not in (0, None) and not e.get("denied") for e in commands),
        "denied_commands": sum(bool(e.get("denied")) for e in commands),
        "guidance_read": sorted(p for p in reads if is_guidance(p)),
        "counters": {name: sum(c.hits(e) for e in commands) for name, c in (counters or {}).items()},
    }
