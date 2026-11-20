# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The note at the top of a report whose run a run-wide error stopped, so a shortened run is not read as a
whole one (https://github.com/intel/dokimi/issues/895)."""

from __future__ import annotations

from typing import Any, List, Mapping

from dokimi.report import fmt


def stop_note(data: Mapping[str, Any]) -> List[str]:
    """The note for the ``terminated`` block at the top of *data* (an arms artifact) or in its
    ``run_metadata`` (an eval file judged from a stopped answers file); none when the run completed."""
    meta = data.get("run_metadata")
    block = data.get("terminated") or (meta.get("terminated") if isinstance(meta, Mapping) else None)
    if not isinstance(block, Mapping):
        return []
    # A CLI's multi-line stderr stays in the note; its own final period (codex's usage limit) is the note's.
    reason = " ".join(fmt.plain(block.get("reason")).split()).rstrip(".")
    # The recorded count includes a question the stop cut short, so it can equal the requested one (4 of 4).
    # A bound, not a count: an eval may judge fewer than the recorded questions (--limit, ungraded ones).
    cause = f": {reason}" if reason else ""  # str() of an error with no message is empty
    return [f"> **Stopped early**{cause}. {fmt.plain(block.get('questions_recorded'))} of "
            f"{fmt.plain(block.get('questions_requested'))} questions have a record, counting any the stop "
            "interrupted. Every number below covers only questions answered before the stop.", ""]


def with_stop(meta: Any, data: Mapping[str, Any]) -> Any:
    """*meta*, a report's copy of the ``run_metadata`` of *data*, with the ``terminated`` block at the top of
    *data* (an arms artifact) when it has none of its own: the Q&A export then records the stop the page notes."""
    if not data.get("terminated"):
        return meta
    return {"terminated": data["terminated"], **(meta or {})}
