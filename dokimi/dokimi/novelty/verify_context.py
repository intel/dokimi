# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The faithfulness prompt and the context it shows besides an atom's cited spans (#1009)."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .spans import Span

_ROW_KINDS = ("list_item", "table_row")
_DELIMITER_ROW = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")

VERIFY_PROMPT = """\
Does the source text below support the statement, exactly as stated?

STATEMENT: {statement}

SOURCE:
{source}

CONTEXT (the heading, the lead-in around the source and the span after a lead-in that ends in
a colon, only for resolving what "this", "both" or a list's lead-in refer to; it never supplies
the claim, so judge the SOURCE alone):
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
    """The heading path, lead-in and table header of the first cited span, or ``(none)``.

    A list item or table row takes the nearest earlier span that is neither; any other
    span takes the span right before it. The lead-in is dropped when it is a heading
    (the heading path already says it) or sits in another section. A table row also gets the header
    row of its own table (the row before its delimiter row), which says what its cells mean. The first cited span in document order has no
    cited span before it, so a cited span is never repeated as context.

    When the last cited span in document order ends in a colon, the very next span is added
    as ``Following:`` (the code block or first item the lead-in points at), unless it is a
    heading (also one that repeats the section's own title) or in another section. The
    next span comes after the last cited one, so it is never cited. Only that one span is taken.
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
    if before >= 0 and spans[before].kind != "heading" \
            and spans[before].heading_path == by_id[first].heading_path:
        parts.append("Lead-in: " + spans[before].text)
    header = _table_header(order[first], spans)
    if header is not None:
        parts.append("Table header: " + spans[header].text)
    last = max(cited, key=order.__getitem__)
    after = order[last] + 1
    if by_id[last].text.rstrip().endswith(":") and after < len(spans):
        following = spans[after]
        if following.kind != "heading" and following.heading_path == by_id[last].heading_path:
            parts.append("Following: " + following.text)
    return "\n".join(parts) or "(none)"


def _is_delimiter(span: Span) -> bool:
    return span.kind == "table_row" and _DELIMITER_ROW.match(span.text) is not None


def _table_header(at: int, spans: list[Span]) -> int | None:
    """The index of the header row of the table the row at ``at`` is in, or ``None``.

    The header is the row before the nearest delimiter row (``|---|---|``) at or before
    ``at`` in the unbroken run of table rows: ``segment`` drops the blank line between two
    tables, so only a delimiter row says where a table begins. A header row itself, and a
    row of a run with no delimiter row, have none.
    """
    if spans[at].kind != "table_row":
        return None
    if at + 1 < len(spans) and _is_delimiter(spans[at + 1]):
        return None
    while at > 0 and spans[at - 1].kind == "table_row":
        if _is_delimiter(spans[at]):
            return at - 1
        at -= 1
    return None
