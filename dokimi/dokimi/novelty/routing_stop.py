# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Which provider errors stop a run (#677, #708); split from ``routing.py``, which stays under its size cap."""
from __future__ import annotations

import sys

from dokimi.llm import _NON_RETRYABLE_MESSAGES, is_retryable

from .client import SpendCapReached

PERMANENT_STOP = "permanent error: "
PERMANENT_REASON = "permanent_error"
# Errors that fail every later call alike: no key, no permission, an unknown model, a parameter every
# call sends. Any other error (a context-length rejection, a refusal, an odd reply) may be one query's.
_RUN_WIDE_ERRORS = frozenset({"authenticationerror", "permissiondeniederror", "notfounderror",
                              "unsupportedparamserror"})


def is_run_wide(exc: Exception) -> bool:
    """Whether *exc* is a permanent provider error that every later call would meet too."""
    if is_retryable(exc):
        return False
    if isinstance(exc, ValueError):  # a reply that would not parse quotes the reply, which may name a credential
        return False
    if isinstance(run_wide := getattr(exc, "run_wide", None), bool):  # set by an error that knows (CliCallError)
        return run_wide
    return type(exc).__name__.lower() in _RUN_WIDE_ERRORS or any(
        marker in str(exc).lower() for marker in _NON_RETRYABLE_MESSAGES)


def permanent_stop(exc: Exception) -> SpendCapReached:
    """The stop a run-wide error makes, in the form a cap stop has: ``reason`` ``permanent_error``."""
    return SpendCapReached(PERMANENT_STOP + (str(exc)[:200] or type(exc).__name__), reason=PERMANENT_REASON)


def stop_if_run_wide(exc: BaseException | None = None) -> None:
    """Raise :func:`permanent_stop` when *exc* (default: the exception being handled) is run-wide.

    Called first in the handler of a per-item ``except Exception``, so that the one failure every
    later call would meet ends the run as a cap stop does, and any other failure stays one item's.
    """
    exc = exc if exc is not None else sys.exc_info()[1]
    if isinstance(exc, Exception) and is_run_wide(exc):
        raise permanent_stop(exc) from exc
