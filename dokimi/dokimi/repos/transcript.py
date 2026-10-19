# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The normalized run record and the helpers every trace format shares.

Kept apart from :mod:`dokimi.repos.events` so the per-harness converters can import
these without importing the dispatcher that imports them back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

OUTPUT_LIMIT = 8192
OUTPUT_KEEP = 4096


@dataclass
class Transcript:
    """What one harness run produced, in harness-independent form."""

    answer: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    cost_usd: float | None = None
    num_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    is_error: bool = False
    harness_version: str | None = None
    result_subtype: str | None = None
    plugins: list[str] = field(default_factory=list)  # loaders the harness reported at start-up
    errors: list[str] = field(default_factory=list)

    def reads(self) -> set[str]:
        return {e["path"] for e in self.events if e["kind"] == "read" and e.get("path")}

    def events_jsonl(self) -> str:
        return "".join(json.dumps(e, sort_keys=True) + "\n" for e in self.events)


def clip_output(text: str) -> tuple[str, bool]:
    """``text`` kept whole up to :data:`OUTPUT_LIMIT` UTF-8 bytes, else its head and tail."""
    data = text.encode("utf-8")
    if len(data) <= OUTPUT_LIMIT:
        return text, False
    head = data[:OUTPUT_KEEP].decode("utf-8", "ignore")
    tail = data[-OUTPUT_KEEP:].decode("utf-8", "ignore")
    # A character cut at either end is dropped whole, so count what was really left out.
    omitted = len(data) - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return f"{head}\n[... {omitted} bytes omitted ...]\n{tail}", True


def relative_path(path: str, workdir: str | None) -> str:
    """``path`` relative to ``workdir`` in POSIX form; POSIX and Windows paths both work."""
    if not workdir:
        return path
    for flavour in (PurePosixPath, PureWindowsPath):
        target, base = flavour(path), flavour(workdir)
        if target.is_absolute() and target.is_relative_to(base):
            return target.relative_to(base).as_posix()
    return path


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
