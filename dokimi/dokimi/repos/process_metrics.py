# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Process metrics of one run, from its ``events.jsonl`` events, for ``result.json`` ``process`` (contract §4.1).

The framework counts what every repository asks of a trace:

- ``tool_calls``: every event of a known kind (:data:`~dokimi.repos.event_format.KNOWN_KINDS`), so every tool call
  of the parent and its subagents, and each ``read`` event kept
  from a shell command, a denied one or one of a failed multi-file read (``cat a missing``) too;
- ``commands``: the ``command`` events;
- ``failed_commands``: commands not denied whose ``rc`` is neither ``0`` nor ``null`` (``null`` means
  no result came back, which is not a failure; a permission denial carries ``rc`` 1 but never ran);
- ``denied_commands``: commands a hook or the harness's permissions refused (``denied``);
- ``guidance_read``: the sorted workspace paths of ``read`` events (a read tool, or a shell command that only
  prints files, ``cat`` / ``head`` / ``tail`` / ``nl`` / ``sed -n``; a pipe, ``grep`` or ``rg`` is not one) not
  denied that the manifest's guidance globs match; a path outside the workspace (absolute, or with ``..``) is
  no repository file. ``repo run`` keeps only the guidance files the workspace held as the agent started (``is_guidance``
  tells them): a multi-file read (``cat a missing``) keeps its reads, as its exit does not say which file failed;
- ``guidance_searched``: the sorted workspace paths, under the same rules as ``guidance_read``, in the ``searched`` list of
  a ``command`` event not denied (a ``grep``, ``egrep``, ``fgrep``, ``rg`` or ``git grep`` that showed lines of the file) or of
  a Claude Code ``Grep`` ``tool`` event (``output_mode`` ``content``). The files are not in ``guidance_read``, and the
  search is no ``read`` event, so ``tool_calls`` does not count it;
- ``unknown_reads``: the relative shell reads the trace made at a directory the converter could not follow
  (after ``source env.sh``, say), which have no ``read`` event: ``guidance_read`` may miss them. It comes from
  the converter (:attr:`~dokimi.repos.transcript.Transcript.unknown_reads`), not from the events.

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

from dokimi.repos.event_format import KNOWN_KINDS

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
                    counters: Mapping[str, Counter] | None = None, unknown_reads: int = 0) -> dict[str, Any]:
    """The ``process`` object of ``result.json`` for one run's events; ``is_guidance`` tells a guidance path."""
    events = list(events)
    commands = [e for e in events if e.get("kind") == "command"]
    # A read that failed (rc 1: the file does not exist) read no guidance.
    reads = {w for e in events if e.get("kind") == "read" and e.get("path") and not e.get("denied")
             and e.get("rc") in (0, None) and (w := _workspace_path(str(e["path"]))) is not None}
    # A search that failed as one search of one file, or that a relative path after a shell move left unknown, has no
    # ``searched`` (the converter drops it); a refused command searched nothing.
    searched = {w for e in events if e.get("searched") and not e.get("denied") for p in e["searched"]
                if (w := _workspace_path(str(p))) is not None}
    return {
        "tool_calls": sum(e.get("kind") in KNOWN_KINDS for e in events),
        "commands": len(commands),
        "failed_commands": sum(e.get("rc") not in (0, None) and not e.get("denied") for e in commands),
        "denied_commands": sum(bool(e.get("denied")) for e in commands),
        "guidance_read": sorted(p for p in reads if is_guidance(p)),
        "guidance_searched": sorted(p for p in searched if is_guidance(p)),
        "unknown_reads": unknown_reads,
        "counters": {name: sum(c.hits(e) for e in commands) for name, c in (counters or {}).items()},
    }
