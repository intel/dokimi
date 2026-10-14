# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Batched model calls that never turn a lost batch into a default value.

Several novelty stages ask one model about many items per call (atoms to classify, atoms
to check for conflicts). A reply that does not parse — most often one cut off by an output
ceiling — used to become ``[]`` or ``continue``, and the items in that batch then read as
an answer: *not needed*, *no conflict*. :func:`ask_in_batches` splits an unparseable batch
in half and re-asks, down to ``min_size`` items, and returns the items it still could not
get an answer for, so the caller can exclude them and say so. A transport failure or an
empty reply is not split: re-asking smaller would only multiply an outage, and a
reasoning model that spent its ceiling before answering does so on half a batch too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .client import LLMClient, accepting

T = TypeVar("T")


@dataclass
class BatchResult(Generic[T]):
    """What came back, and what did not.

    Attributes:
        answered: ``(chunk, parsed reply)`` pairs, in item order.
        failed: Items whose call failed, came back empty, or never parsed.
        too_small: Items left in a chunk below ``min_size``, which is never sent (a
            conflict check of one statement has no pair to look at).
        retried_batches: Chunks whose reply did not parse and were split.
        calls: Calls made, splits included.
        last_error: The last error on an item that ended in ``failed``. Errors that a
            split recovered from are not recorded, so this never blames a cause that
            lost nothing.
    """

    answered: list[tuple[list[T], Any]] = field(default_factory=list)
    failed: list[T] = field(default_factory=list)
    too_small: list[T] = field(default_factory=list)
    retried_batches: int = 0
    calls: int = 0
    last_error: str = ""


def ask_in_batches(
    client: LLMClient,
    items: list[T],
    *,
    size: int,
    prompt: Callable[[list[T]], str],
    parse: Callable[[str], Any],
    system: str | None = None,
    min_size: int = 1,
) -> BatchResult[T]:
    """Ask ``prompt(chunk)`` for chunks of ``items``; split a chunk whose reply won't parse.

    Answers come back in item order: a split chunk's halves are re-asked in its place.
    ``parse`` must raise on a reply it cannot use (the JSON extractors in
    :mod:`dokimi.llm` raise ``ValueError``); it doubles as the response-cache acceptance
    test, so a reply it rejects is never stored or replayed. A chunk with fewer than
    ``min_size`` items is not sent; its items go to ``too_small``.
    """
    result: BatchResult[T] = BatchResult()
    usable = accepting(parse)
    pending = [items[i:i + size] for i in range(0, len(items), size)]
    while pending:
        chunk = pending.pop(0)
        if len(chunk) < min_size:
            result.too_small.extend(chunk)
            continue
        replied = False
        try:
            result.calls += 1
            reply = client.text(prompt(chunk), system=system, accept=usable)
            if not reply.strip():
                raise ValueError("the model returned an empty reply")
            replied = True
            result.answered.append((chunk, parse(reply)))
        except Exception as exc:
            if replied and len(chunk) > 1:
                half = len(chunk) // 2
                pending[:0] = [chunk[:half], chunk[half:]]
                result.retried_batches += 1
            else:
                result.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                result.failed.extend(chunk)
    return result
