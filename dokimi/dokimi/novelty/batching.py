# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Batched model calls that never turn a lost batch into a default value.

Several novelty stages ask one model about many items per call (atoms to classify, atoms
to check for conflicts). A reply that does not parse — most often one cut off by an output
ceiling — used to become ``[]`` or ``continue``, and the items in that batch then read as
an answer: *not needed*, *no conflict*. :func:`ask_in_batches` splits a batch whose reply is unparseable or
unusable (:class:`UnusableReply`) in half and re-asks, as long as each half keeps ``min_size`` items, and returns the items
it still could not get an answer for, so the caller can exclude them and say so. A
transport failure or an empty reply is not split: re-asking smaller would only multiply
an outage, and a reasoning model that spent its ceiling before answering does so on half
a batch too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Generic, TypeVar

from .client import LLMClient, OutputTruncated, accepting

T = TypeVar("T")


class UnusableReply(ValueError):
    """The reply parsed, but holds no row this stage can use: a shape check, not a parse error."""


def retry_wording(retried: int, unusable: int) -> tuple[str, str]:
    """The label and the advice for batches re-asked as halves, split by cause.

    Only a cut-off reply is cured by a higher ``--max-tokens``; one whose rows are all
    unusable is not, so the advice is given only for the rest.
    """
    cut = retried - unusable
    if not cut:
        label = "unusable"
    elif unusable:
        label = "unparseable or unusable"
    else:
        label = "unparseable"
    return label, ("; a higher --max-tokens avoids the extra calls" if cut else "")


@dataclass
class BatchResult(Generic[T]):
    """What came back, and what did not.

    Attributes:
        answered: ``(chunk, parsed reply)`` pairs, in item order.
        failed: Items whose call failed, came back empty, or never parsed (including a
            chunk too small to split into two halves of ``min_size``).
        too_small: Items in an initial chunk below ``min_size`` (the remainder after
            cutting ``items`` into ``size`` chunks), which is never sent: a conflict
            check of one statement has no pair to look at. A split never produces one.
        retried_batches: Chunks whose reply did not parse, or had no usable row, and were
            split.
        unusable_retries: The subset of ``retried_batches`` whose reply parsed but failed
            ``check``.
        calls: Calls made, splits included.
        unusable: The items of ``failed`` whose reply parsed and had no usable row, as
            opposed to a call that failed.
        last_error: The last transport or parse error on an item that ended in ``failed``;
            an unusable reply is counted in ``unusable`` instead. Errors that a
            split recovered from are not recorded, so this never blames a cause that
            lost nothing.
    """

    answered: list[tuple[list[T], Any]] = field(default_factory=list)
    failed: list[T] = field(default_factory=list)
    unusable: list[T] = field(default_factory=list)
    too_small: list[T] = field(default_factory=list)
    retried_batches: int = 0
    unusable_retries: int = 0
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
    check: Callable[[list[T], Any], None] | None = None,
) -> BatchResult[T]:
    """Ask ``prompt(chunk)`` for chunks of ``items``; split a chunk whose reply won't parse or can't be used.

    Answers come back in item order: a split chunk's halves are re-asked in its place.
    ``parse`` must raise on a reply it cannot use (the JSON extractors in
    :mod:`dokimi.llm` raise ``ValueError``). ``check(chunk, rows)`` may raise
    :class:`UnusableReply` for a parsed reply with no row this chunk can use. Both run
    before the reply is answered, and they are the response-cache acceptance test, so a
    reply they reject is never stored or replayed. A chunk whose reply is rejected is split
    only if both halves keep ``min_size`` items; otherwise its items go to ``failed`` with
    the error. An initial chunk with fewer than ``min_size`` items is not sent; its items
    go to ``too_small``.
    """

    def read(chunk: list[T], reply: str) -> Any:
        rows = parse(reply)
        if check is not None:
            check(chunk, rows)
        return rows

    result: BatchResult[T] = BatchResult()
    pending = [items[i:i + size] for i in range(0, len(items), size)]
    while pending:
        chunk = pending.pop(0)
        if len(chunk) < min_size:
            result.too_small.extend(chunk)
            continue
        replied = False
        try:
            result.calls += 1
            reply = client.text(
                prompt(chunk), system=system, accept=accepting(partial(read, chunk)),
            )
            if not reply.strip():
                raise ValueError("the model returned an empty reply")
            replied = True
            result.answered.append((chunk, read(chunk, reply)))
        except Exception as exc:
            half = len(chunk) // 2
            # A reply cut at the ceiling is re-asked smaller, like one that will not parse.
            if (replied or isinstance(exc, OutputTruncated)) and half >= max(min_size, 1):
                pending[:0] = [chunk[:half], chunk[half:]]
                result.retried_batches += 1
                result.unusable_retries += isinstance(exc, UnusableReply)
            else:
                result.failed.extend(chunk)
                if isinstance(exc, UnusableReply):
                    result.unusable.extend(chunk)
                else:
                    # an unusable reply is counted under its own cause; keep the transport error
                    result.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
    return result
