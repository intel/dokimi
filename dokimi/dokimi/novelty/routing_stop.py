# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Which provider errors stop a routing run (#677); split from ``routing.py``, which stays under its size cap."""
from __future__ import annotations

from dokimi.llm import _NON_RETRYABLE_MESSAGES, is_retryable

PERMANENT_STOP = "permanent error: "
# Errors that fail every later call alike: no key, no permission, an unknown model. Any other
# error (a context-length rejection, a refusal, an odd reply) may belong to one query (#709).
_RUN_WIDE_ERRORS = frozenset({"authenticationerror", "permissiondeniederror", "notfounderror"})
_NO_CREDENTIALS = (*_NON_RETRYABLE_MESSAGES, "unable to locate credentials")


def is_run_wide(exc: Exception) -> bool:
    """Whether *exc* is a permanent provider error that every later call would meet too."""
    if is_retryable(exc):
        return False
    return type(exc).__name__.lower() in _RUN_WIDE_ERRORS or any(
        marker in str(exc).lower() for marker in _NO_CREDENTIALS)
