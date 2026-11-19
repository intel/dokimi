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
    reason = " ".join(fmt.plain(block.get("reason")).split())  # a CLI's multi-line stderr stays in the note
    # A bound, not a count: an eval may judge fewer than the recorded questions (--limit, ungraded ones).
    return [f"> **Stopped early** after {fmt.plain(block.get('questions_recorded'))} of "
            f"{fmt.plain(block.get('questions_requested'))} questions: {reason}. "
            "Every number below covers only questions answered before the stop.", ""]
