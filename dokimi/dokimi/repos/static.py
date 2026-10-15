# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo_static.v1``: what a repository's ``static`` entry point writes (contract §4.4).

The framework reads only ``file``, ``line`` and ``status`` per claim, so any
repository's own checks can be merged into L0 without knowing their rules.
A status such as ``ok_basename`` is a refinement of its class, ``ok``.
"""

from __future__ import annotations

from typing import Any

from dokimi.artifacts import schema_errors

SCHEMA_VERSION = "repo_static.v1"
STATUS_CLASSES = ("ok", "unresolved", "fail")


def check_static(doc: Any) -> list[str]:
    """Problems with a ``static`` output document; empty when it is valid."""
    return schema_errors(SCHEMA_VERSION, doc)


def status_class(status: str) -> str:
    """``ok``, ``unresolved`` or ``fail``: the part of ``status`` before the first ``_``."""
    return status.split("_", 1)[0]


def static_counts(doc: dict[str, Any]) -> dict[str, int]:
    """Claims per status class, for a document that passed :func:`check_static`."""
    counts = dict.fromkeys(STATUS_CLASSES, 0)
    for claim in doc["claims"]:
        counts[status_class(claim["status"])] += 1
    return counts
