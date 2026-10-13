# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Atom extraction under a truncating or failing extractor."""

from __future__ import annotations

import json
import re

import pytest

from dokimi.novelty.atoms import ExtractionError, ExtractionReport, extract_atoms
from dokimi.novelty.client import LLMClient
from dokimi.novelty.spans import segment

DOC = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 11))


def _rows(prompt: str) -> list[dict]:
    return [
        {"statement": f"{sid} holds a default.", "question": f"What is the default of {sid}?",
         "answer": "a value", "kind": "proposition", "tags": ["local"], "span_ids": [sid]}
        for sid in re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)
    ]


def _ceiling(max_spans: int, calls: list[int], bad: frozenset[str] = frozenset()):
    """A transport that truncates its reply mid-array above ``max_spans`` spans, the way
    a provider's default output ceiling does, and always garbles the ``bad`` spans."""

    def transport(prompt, system):
        rows = _rows(prompt)
        calls.append(len(rows))
        reply = json.dumps(rows)
        if len(rows) > max_spans or bad & {r["span_ids"][0] for r in rows}:
            reply = reply[: len(reply) // 2]
        return reply, None

    return transport


def test_a_truncated_batch_is_split_and_every_span_still_yields_an_atom():
    spans = segment(DOC)
    calls: list[int] = []
    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor", transport=_ceiling(3, calls))
    atoms = extract_atoms(spans, client=client, batch_spans=10, report=report)
    assert {a.span_ids[0] for a in atoms} == {s.id for s in spans}
    assert [a.span_ids[0] for a in atoms] == [s.id for s in spans]  # document order kept
    assert report.dropped_span_ids == []
    assert report.retried_batches > 0
    assert report.warning(len(spans)) is None


def test_a_span_that_never_parses_is_reported_not_silently_lost():
    spans = segment(DOC)
    bad = spans[4].id
    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor",
                       transport=_ceiling(25, [], bad=frozenset({bad})))
    atoms = extract_atoms(spans, client=client, report=report)
    assert bad not in {a.span_ids[0] for a in atoms}
    assert len(atoms) == len(spans) - 1
    assert report.dropped_span_ids == [bad]
    assert report.dropped_tokens == spans[4].tokens
    warning = report.warning(len(spans))
    assert warning and f"1 of {len(spans)} spans" in warning


def test_a_transport_failure_is_not_multiplied_by_splitting():
    spans = segment(DOC)
    calls: list[int] = []

    def down(prompt, system):
        calls.append(1)
        raise RuntimeError("model is not available on this provider")

    client = LLMClient(model="x", role="extractor", transport=down)
    with pytest.raises(ExtractionError, match="not available"):
        extract_atoms(spans, client=client, batch_spans=5)
    assert len(calls) == 2  # one call per batch, no halving


def test_a_partial_transport_failure_names_the_lost_spans():
    spans = segment(DOC)
    first_batch = {s.id for s in spans[:5]}

    def flaky(prompt, system):
        if first_batch & set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)):
            raise RuntimeError("throttled")
        return json.dumps(_rows(prompt)), None

    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor", transport=flaky)
    atoms = extract_atoms(spans, client=client, batch_spans=5, report=report)
    assert len(atoms) == 5
    assert set(report.dropped_span_ids) == first_batch
    assert "throttled" in report.last_error
