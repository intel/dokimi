# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The faithfulness prompt and the context it shows besides an atom's cited spans (#1009)."""

from __future__ import annotations

from collections.abc import Iterable

from .spans import Span

_ROW_KINDS = ("list_item", "table_row")

VERIFY_PROMPT = """\
Does the source text below support the statement, exactly as stated?

STATEMENT: {statement}

SOURCE:
{source}

CONTEXT (the heading and the lead-in around the source, only for resolving what "this",
"both" or a list's lead-in refer to; it never supplies the claim, so judge the SOURCE alone):
{context}

Answer with JSON only:
{{"verdict": "supported" | "distorted" | "unsupported", "why": "<one clause>"}}

- "supported": the source asserts it, with the same qualifiers, numbers and polarity.
- "distorted": the source says something close but the statement changes a qualifier,
  a number, a version, or the polarity.
- "unsupported": the source does not assert it at all.

The statement must be asserted by the SOURCE: a polarity, number or qualifier that differs
from the source is "distorted" or "unsupported" even when the context words it otherwise.
"""


def context_for(span_ids: Iterable[str], spans: list[Span], by_id: dict[str, Span]) -> str:
    """The heading path and lead-in of the first cited span, or ``(none)``.

    A list item or table row takes the nearest earlier span that is neither; any other
    span takes the span right before it. The first cited span in document order has no
    cited span before it, so a cited span is never repeated as context.
    """
    order = {span.id: i for i, span in enumerate(spans)}
    cited = [s for s in span_ids if s in by_id and s in order]
    if not cited:
        return "(none)"
    first = min(cited, key=order.__getitem__)
    parts = []
    if by_id[first].heading_path:
        parts.append("Heading: " + " > ".join(by_id[first].heading_path))
    before = order[first] - 1
    if by_id[first].kind in _ROW_KINDS:
        while before >= 0 and spans[before].kind in _ROW_KINDS:
            before -= 1
    if before >= 0:
        parts.append("Lead-in: " + spans[before].text)
    return "\n".join(parts) or "(none)"
