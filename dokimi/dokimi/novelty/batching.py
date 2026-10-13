# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Batched model calls that never turn a lost batch into a default value.

Several novelty stages ask one model about many items per call (atoms to classify, atoms
to check for conflicts). A reply that does not parse — most often one cut off by an output
ceiling — used to become ``[]`` or ``continue``, and the items in that batch then read as
an answer: *not needed*, *no conflict*. :func:`ask_in_batches` splits an unparseable batch
in half and re-asks, down to one item, and returns the items it still could not get an
answer for, so the caller can exclude them and say so. A transport failure is not split:
re-asking smaller would only multiply an outage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .client import LLMClient

T = TypeVar("T")


@dataclass
class BatchResult(Generic[T]):
    """What came back, and what did not."""

    answered: list[tuple[list[T], Any]] = field(default_factory=list)
    failed: list[T] = field(default_factory=list)
    retried_batches: int = 0
    last_error: str = ""


def ask_in_batches(
    client: LLMClient,
    items: list[T],
    *,
    size: int,
    prompt: Callable[[list[T]], str],
    parse: Callable[[str], Any],
    system: str | None = None,
) -> BatchResult[T]:
    """Ask ``prompt(chunk)`` for chunks of ``items``; split a chunk whose reply won't parse.

    Answers come back in item order. ``parse`` must raise on a reply it cannot use; the
    JSON extractors in :mod:`dokimi.llm` raise ``ValueError``.
    """
    result: BatchResult[T] = BatchResult()
    pending = [items[i:i + size] for i in range(0, len(items), size)]
    while pending:
        chunk = pending.pop(0)
        replied = False
        try:
            reply = client.text(prompt(chunk), system=system)
            replied = True
            result.answered.append((chunk, parse(reply)))
        except Exception as exc:
            result.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            if replied and len(chunk) > 1:
                half = len(chunk) // 2
                pending[:0] = [chunk[:half], chunk[half:]]
                result.retried_batches += 1
            else:
                result.failed.extend(chunk)
    return result
