# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The per-model-call events of ``events.jsonl``: ``turn`` and ``compaction`` (docs/events-format.md).

A ``turn`` is one model call and the tokens it was billed: ``input`` (uncached), ``output``,
``cache_read``, ``cache_write`` and ``prompt_tokens`` (the whole prompt, ``input + cache_read +
cache_write``). Claude's come from :mod:`dokimi.context.trace`, the parse the run's own token totals
use, so the turns of a complete trace sum to those totals. Codex's come from its ``turn.completed``
usage. Copilot's trace carries no usage, so it has no turn events. A count the harness did not report
is ``null``, never ``0``.

A ``compaction`` follows the turn it came after; its ``turn`` is that turn's index, or ``null`` before
the first call.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from dokimi.context.trace import ContextTrace

from .transcript import _parse_time


def claude_events(trace: ContextTrace, start: datetime | None) -> list[dict[str, Any]]:
    """The turn events of every model call (a subagent's carry ``agent``), then the compactions."""
    events: list[dict[str, Any]] = []
    for call in trace.calls:
        seen = set(call["reported"])
        prompt = seen & {"input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"}
        event: dict[str, Any] = {
            "kind": "turn", "t": _seconds(call["at"], start), "turn": call["i"],
            "input": call["input_tokens"] if "input_tokens" in seen else None,
            "output": call["output_tokens"] if "output_tokens" in seen else None,
            "cache_read": call["cache_read_tokens"] if "cache_read_input_tokens" in seen else None,
            "cache_write": call["cache_write_tokens"] if "cache_creation_input_tokens" in seen else None,
            "prompt_tokens": (call["input_tokens"] + call["cache_read_tokens"] + call["cache_write_tokens"]
                              if prompt else None),
        }
        if call["agent"]:
            event["agent"] = str(call["agent"])
        events.append(event)
    for compaction in trace.compactions:
        after = compaction["after_call"]
        events.append({"kind": "compaction", "t": _seconds(compaction["at"], start),
                       "turn": after if after >= 0 else None})
    return events


def codex_turn(turn: int, usage: Mapping[str, Any]) -> dict[str, Any]:
    """The turn event of one Codex ``turn.completed`` usage. Codex counts the cached part inside
    ``input_tokens``, so ``input`` is the uncached rest and ``prompt_tokens`` the reported total.
    A usage with no tokens is no measured turn: every count is ``null``."""
    fresh = int(usage.get("input_tokens") or 0)
    produced = int(usage.get("output_tokens") or 0)
    cached = max(int(usage.get("cached_input_tokens") or 0), 0)  # a negative count is malformed, not a credit
    measured = bool(fresh or produced or cached)  # a missing or all-zero usage is not a measured zero
    has_input = measured and usage.get("input_tokens") is not None  # each count is null on its own when absent
    has_output = measured and usage.get("output_tokens") is not None
    has_cached = measured and usage.get("cached_input_tokens") is not None  # reported, so a zero is measured
    return {
        "kind": "turn", "t": None, "turn": turn,
        "input": max(fresh - cached, 0) if has_input else None,
        "output": produced if has_output else None,
        "cache_read": cached if has_cached else None,
        "cache_write": None,  # Codex reports no cache writes
        "prompt_tokens": fresh if has_input else None,
    }


def _seconds(stamp: Any, start: datetime | None) -> float | None:
    when = _parse_time(stamp)
    if when is None or start is None:
        return None
    return round((when - start).total_seconds(), 3)
