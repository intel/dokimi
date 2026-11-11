# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The ``--json`` document of a leaf: ``cli_output.v1`` (issue #94 §2.7, stage F2).

A leaf that opts in prints exactly one document on stdout when it succeeds, on an input error (exit
2) and on a crash (exit 5). Logs go to stderr. ``data`` holds the leaf's own payload; every other
key has the meaning the proposal gives it. The schema is ``dokimi/schemas/cli_output.v1.json``.
"""

from __future__ import annotations

import json
from typing import Any

from dokimi._version import __version__
from dokimi import artifacts
from dokimi.artifacts import SCHEMA_DIR
from dokimi.utils import InputError

SCHEMA_VERSION = "cli_output.v1"
STATUSES = ("complete", "partial", "budget_stopped", "cancelled", "failed")
ERROR_CODES = ("moved", "input", "no_credentials", "infra")
#: The exit code of each error code, as §2.6 gives them. ``moved`` and ``input`` are exit 2.
EXIT_OF_ERROR = {"moved": 2, "input": 2, "no_credentials": 2, "infra": 5}


def _zero_cost() -> dict[str, Any]:
    """A leaf that makes no model call: nothing spent, no cap."""
    return {"spent_usd": 0.0, "cap_usd": None, "remaining_usd": None, "estimate_kind": None,
            "calls": 0, "priced": None}


def current_run_id() -> str | None:
    """The run id this process writes, or None when ``DOKIMI_RUN_ID`` is malformed: the error path must not raise again."""
    try:
        return artifacts.run_id()
    except InputError:
        return None


def envelope(kind: str, *, run_id: str | None = None, status: str = "complete", verdict: str | None = None,
             reason: str | None = None, error: dict[str, Any] | None = None,
             summary: dict[str, Any] | None = None, subjects: list[dict[str, Any]] | None = None,
             artifacts: dict[str, Any] | None = None, cost: dict[str, Any] | None = None,
             limits: list[dict[str, Any]] | None = None, next_steps: list[dict[str, Any]] | None = None,
             data: Any = None) -> dict[str, Any]:
    """One ``cli_output.v1`` document. ``verdict=None`` means the leaf issues no verdict."""
    if status not in STATUSES:
        raise ValueError(f"unknown cli_output status {status!r}")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "run_id": run_id,
        "status": status,
        "verdict": verdict,
        "reason": reason,
        "error": error,
        "summary": {"dokimi_version": __version__, **(summary or {})},
        "subjects": list(subjects or []),
        "artifacts": artifacts,
        "cost": {**_zero_cost(), **(cost or {})},
        "limits": list(limits or []),
        "next": list(next_steps or []),
        "data": data,
    }


def error_document(kind: str, code: str, message: str, *, field: str | None = None,
                   run_id: str | None = None) -> dict[str, Any]:
    """The document of a run that stopped on ``code`` (one of :data:`ERROR_CODES`)."""
    if code not in ERROR_CODES:
        raise ValueError(f"unknown cli_output error code {code!r}")
    return envelope(kind, run_id=run_id, status="failed", error={"code": code, "message": message,
                                                  "retryable": code == "infra", "field": field})


def emit(doc: dict[str, Any]) -> None:
    """Print the one document on stdout."""
    print(json.dumps(doc, indent=2))


def emit_leaf(kind: str, data: Any, **fields: Any) -> None:
    """Print a leaf's success document: its payload under ``data``, this process's run id."""
    emit(envelope(kind, run_id=current_run_id(), data=data, **fields))


def schema_document() -> dict[str, Any]:
    """The schema itself, for ``dokimi data schema show cli_output.v1``."""
    return json.loads((SCHEMA_DIR / f"{SCHEMA_VERSION}.json").read_text(encoding="utf-8"))
