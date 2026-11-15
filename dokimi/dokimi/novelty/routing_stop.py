# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Which provider errors stop a routing run (#677); split from ``routing.py``, which stays under its size cap."""
from __future__ import annotations

from dokimi.llm import _NON_RETRYABLE_MESSAGES, is_retryable

PERMANENT_STOP = "permanent error: "
# Errors that fail every later call alike: no key, no permission, an unknown model, a parameter every
# call sends. Any other error (a context-length rejection, a refusal, an odd reply) may be one query's.
_RUN_WIDE_ERRORS = frozenset({"authenticationerror", "permissiondeniederror", "notfounderror",
                              "unsupportedparamserror"})


def is_run_wide(exc: Exception) -> bool:
    """Whether *exc* is a permanent provider error that every later call would meet too."""
    if is_retryable(exc):
        return False
    if isinstance(run_wide := getattr(exc, "run_wide", None), bool):  # set by an error that knows (CliCallError)
        return run_wide
    return type(exc).__name__.lower() in _RUN_WIDE_ERRORS or any(
        marker in str(exc).lower() for marker in _NON_RETRYABLE_MESSAGES)
