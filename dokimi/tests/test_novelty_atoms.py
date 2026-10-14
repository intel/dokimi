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
    assert report.unread_span_ids == [] and not any(s.unread for s in spans)
    assert report.retried_batches > 0
    # Nothing was lost, but re-asking cost calls, and the run says how many.
    assert (report.calls, report.batches) == (len(calls), 1)
    assert report.warnings(len(spans)) == [
        f"atom extraction re-asked {report.retried_batches} unparseable batches as halves: "
        f"{len(calls)} extraction calls where 1 would do without splitting; a higher "
        "--max-tokens avoids the extra calls"
    ]


def test_a_span_that_never_parses_is_reported_not_silently_lost():
    spans = segment(DOC)
    bad = spans[4].id
    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor",
                       transport=_ceiling(25, [], bad=frozenset({bad})))
    atoms = extract_atoms(spans, client=client, report=report)
    # Only the first half of the first split fails, so re-asking at the end of the queue
    # instead of in place would put the second half's atoms first.
    assert [a.span_ids[0] for a in atoms] == [s.id for s in spans if s.id != bad]
    assert report.unread_span_ids == report.unparseable_span_ids == [bad]
    assert report.unread_tokens == spans[4].tokens
    assert [s.id for s in spans if s.unread] == [bad]
    lost = report.warnings(len(spans))[0]
    assert lost.startswith(f"atom extraction could not read 1 of {len(spans)} spans")
    assert "did not parse even one span per call" in lost and "raise --max-tokens" in lost
    assert "lost to calls that failed" not in lost


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
    assert set(report.unread_span_ids) == first_batch
    lost = report.warnings(len(spans))[0]
    assert "5 were lost to calls that failed or came back empty" in lost
    assert "RuntimeError: throttled" in lost
    assert "--max-tokens" not in lost  # a failed call is not an output ceiling


def test_a_recovered_parse_error_does_not_hide_the_failure_that_lost_spans():
    """Batch 1 is lost to a bad key; batch 2 is truncated and fully recovered."""
    spans = segment(DOC)
    first_batch = {s.id for s in spans[:5]}

    class AuthenticationError(Exception):
        pass

    def transport(prompt, system):
        ids = re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)
        if first_batch & set(ids):
            raise AuthenticationError("invalid api key")
        reply = json.dumps(_rows(prompt))
        return (reply[:-3] if len(ids) > 2 else reply), None

    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor", transport=transport)
    atoms = extract_atoms(spans, client=client, batch_spans=5, report=report)
    assert len(atoms) == 5 and report.retried_batches > 0
    lost = report.warnings(len(spans))[0]
    assert "AuthenticationError: invalid api key" in lost
    assert "did not parse" not in lost and "JSONDecodeError" not in lost
    assert report.last_error == "AuthenticationError: invalid api key"


def test_recovered_replies_that_assert_nothing_are_not_an_extraction_failure():
    spans = segment(DOC)

    def preamble(prompt, system):
        ids = re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)
        return ("Here you go: [" if len(ids) > 1 else "[]"), None

    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=preamble),
                          report=report)
    assert atoms == [] and report.unread_span_ids == []


def test_an_empty_reply_is_not_split():
    spans = segment(DOC)
    calls: list[int] = []

    def starved(prompt, system):
        calls.append(1)
        return "", None

    report = ExtractionReport()
    with pytest.raises(ExtractionError, match="empty reply"):
        extract_atoms(spans, client=LLMClient(model="x", transport=starved),
                      batch_spans=5, report=report)
    assert len(calls) == 2
    assert len(report.unread_span_ids) == len(spans) and report.unparseable_span_ids == []


def _half_lost(n: int = 50):
    """``n`` one-sentence spans, every one carrying an atom, the first batch lost."""
    doc = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, n + 1))
    spans = segment(doc)
    first_batch = {s.id for s in spans[:25]}

    def throttled_first(prompt, system):
        if first_batch & set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)):
            raise RuntimeError("RateLimitError: slow down")
        return json.dumps(_rows(prompt)), None

    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=throttled_first),
                          report=report)
    return spans, atoms, report


def test_unread_spans_are_not_counted_as_carrying_nothing():
    from dokimi.gate.novelty_gate import evaluate_novelty
    from dokimi.novelty.attribute import attribute_spans, dead_weight
    from dokimi.novelty.spans import span_summary

    spans, atoms, report = _half_lost()
    summary = span_summary(spans, atoms)
    assert summary["frac_carrying_nothing"] == 0.0
    assert summary["tokens_carrying_nothing"] == 0
    assert (summary["n_unread"], summary["tokens_unread"]) == (25, report.unread_tokens)

    rows = attribute_spans(spans, atoms)
    assert dead_weight(rows) == []
    assert {r.label for r in rows[:25]} == {"unread"}

    check = next(c for c in evaluate_novelty({"spans": summary}).checks
                 if c.name == "dead_tokens")
    assert check.passed and check.measured
    assert "25 spans" in check.message and "never read by extraction" in check.message


def test_the_shortener_never_cuts_an_unread_span():
    from dokimi.novelty.retention import RetentionOracle
    from dokimi.novelty.shorten import build_candidates

    spans, atoms, _report = _half_lost()
    unread = {s.id for s in spans if s.unread}
    candidates = build_candidates(spans, atoms, oracle=RetentionOracle(), budgets=(0.5,))
    for candidate in candidates:
        assert unread <= set(candidate.kept_span_ids), candidate.strategy


def test_the_unread_record_survives_atoms_json(tmp_path):
    from dokimi.novelty.atoms import load_atoms, save_atoms

    spans, atoms, report = _half_lost()
    path = tmp_path / "atoms.json"
    save_atoms(atoms, spans, path, extraction=report)
    record = json.loads(path.read_text())["extraction"]
    assert record["unread_span_ids"] == [s.id for s in spans[:25]]
    assert record["unread_tokens"] == report.unread_tokens
    assert record["last_error"].startswith("RuntimeError: RateLimitError")
    _atoms, loaded = load_atoms(path)
    assert [s.id for s in loaded if s.unread] == record["unread_span_ids"]
