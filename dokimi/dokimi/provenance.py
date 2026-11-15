# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The optional ``version`` and ``provenance`` of a task and of a question-set item.

``provenance`` says where the record came from and how far it has been checked:
``source`` is ``hand-written``, ``generated:<tool>@<dokimi version>`` or ``imported:<format>``;
``reviewed_by`` names the reviewer; ``status`` is ``candidate``, ``validated`` or ``frozen``.
Every key is optional and an absent one is *unknown*, never an error. ``questions.v2`` declares
the same shape in ``$defs.provenance``; ``tests/test_provenance.py`` keeps the two in agreement.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from dokimi._version import __version__

UNKNOWN = "unknown"
STATUSES = ("candidate", "validated", "frozen")
KEYS = ("source", "reviewed_by", "status")
SOURCE_RE = re.compile(r"^(hand-written|generated:[A-Za-z0-9_.-]+@[A-Za-z0-9_.+-]+|imported:[A-Za-z0-9_.-]+)$")


class ProvenanceError(ValueError):
    """A ``version`` or ``provenance`` value of the wrong shape."""


def parse_version(raw: Any) -> str | int | None:
    """The ``version`` value (a non-empty string or an integer), or None when absent; :class:`ProvenanceError`
    otherwise. Integers are allowed because task.json is open and some repositories already write ``"version": 2``."""
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip():
        return raw
    raise ProvenanceError(f"version must be a non-empty string or an integer, got {raw!r}")


def parse_provenance(raw: Any) -> dict[str, Any] | None:
    """The ``provenance`` block, checked, or None when absent; :class:`ProvenanceError` on a bad shape."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ProvenanceError(f"provenance must be an object with optional keys {', '.join(KEYS)}")
    unknown = sorted(set(raw) - set(KEYS))
    if unknown:
        raise ProvenanceError(f"provenance: unknown key(s) {', '.join(map(str, unknown))} "
                              f"(known: {', '.join(KEYS)})")
    for key in KEYS:
        if key in raw and raw[key] is None:
            raise ProvenanceError(f"provenance.{key} is null: omit the key instead")
    source = raw.get("source")
    if source is not None and not (isinstance(source, str) and SOURCE_RE.fullmatch(source)):
        raise ProvenanceError(f"provenance.source must be hand-written, generated:<tool>@<version> or "
                              f"imported:<format>, got {source!r}")
    reviewer = raw.get("reviewed_by")
    if reviewer is not None and not (isinstance(reviewer, str) and reviewer.strip()):
        raise ProvenanceError(f"provenance.reviewed_by must be a non-empty string, got {reviewer!r}")
    status = raw.get("status")
    if status is not None and status not in STATUSES:
        raise ProvenanceError(f"provenance.status must be one of {', '.join(STATUSES)}, got {status!r}")
    return dict(raw)


def status_of(record: Mapping[str, Any]) -> str:
    """The record's ``provenance.status``, or :data:`UNKNOWN` when it has none or an invalid one."""
    block = record.get("provenance")
    status = block.get("status") if isinstance(block, Mapping) else None
    return status if status in STATUSES else UNKNOWN


def generated_by(tool: str) -> dict[str, Any]:
    """The ``provenance`` a generator stamps on what it writes: ``generated:dokimi-<tool>@<dokimi version>``."""
    return {"source": f"generated:dokimi-{tool}@{__version__}"}
