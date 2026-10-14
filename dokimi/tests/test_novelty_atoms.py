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
    assert "5 were lost to calls that failed, which are not split" in lost
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
    assert report.empty_span_ids == report.unread_span_ids


def test_an_empty_reply_is_its_own_cause_with_the_max_tokens_hint():
    spans = segment(DOC)
    first_batch = {s.id for s in spans[:5]}

    def starved_first(prompt, system):
        if first_batch & set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)):
            return "", None
        return json.dumps(_rows(prompt)), None

    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=starved_first),
                          batch_spans=5, report=report)
    assert len(atoms) == 5 and set(report.empty_span_ids) == first_batch
    assert report.calls == 2 and report.retried_batches == 0  # not split
    tokens = sum(s.tokens for s in spans[:5])
    assert report.warnings(len(spans)) == [
        f"atom extraction could not read 5 of {len(spans)} spans ({tokens} tokens): 5 got an "
        "empty reply, which a reasoning model returns when it spends the output ceiling "
        "before answering, so raise --max-tokens (empty replies are not split: half a batch "
        "comes back empty too). Unread spans are left out of `frac_carrying_nothing` and "
        "dead weight, labelled `unread`, kept by every shortening candidate, and listed in "
        "`extraction.unread_span_ids`; whatever they assert is missing from every rate"
    ]
    # No private class name, and no "failed call" cause for a call that worked.
    assert "_EmptyReply" not in report.last_error
    assert report.last_error == "the extractor returned an empty reply"
    assert report.as_dict()["last_error"] == report.last_error


def test_a_capped_extraction_counts_only_the_batches_it_reached():
    """200 spans, 3 atoms each; every 25-span reply is cut and both halves parse."""
    doc = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 201))
    spans = segment(doc)

    def three_per_span_cut_above_13(prompt, system):
        ids = re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)
        rows = [dict(row, statement=f"{row['statement']} ({k})",
                     question=f"q{k} {row['span_ids'][0]}")
                for row in _rows(prompt) for k in range(3)]
        reply = json.dumps(rows)
        return (reply[:-2] if len(ids) > 13 else reply), None

    report = ExtractionReport()
    client = LLMClient(model="x", transport=three_per_span_cut_above_13)
    atoms = extract_atoms(spans, client=client, cap=120, report=report)
    assert len(atoms) == 120
    # Two 25-span batches reached the cap: each cost one cut call and two halves.
    assert (report.calls, report.batches, report.retried_batches) == (6, 2, 2)
    assert (
        "atom extraction re-asked 2 unparseable batches as halves: 6 extraction calls where "
        "2 would do without splitting; a higher --max-tokens avoids the extra calls"
    ) in report.warnings(len(spans))


def _half_lost(n: int = 50, silent: frozenset[int] = frozenset()):
    """``n`` one-sentence spans, the first batch lost; every read span carries an atom
    except those at the ``silent`` indexes, which are read and assert nothing."""
    doc = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, n + 1))
    spans = segment(doc)
    first_batch = {s.id for s in spans[:25]}
    quiet = {spans[i].id for i in silent}

    def throttled_first(prompt, system):
        if first_batch & set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)):
            raise RuntimeError("RateLimitError: slow down")
        return json.dumps([r for r in _rows(prompt) if r["span_ids"][0] not in quiet]), None

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

    # Half the text unread is below the gate's read-share floor: reported, not judged.
    check = next(c for c in evaluate_novelty({"spans": summary}).checks
                 if c.name == "dead_tokens")
    assert check.passed and not check.measured
    assert "25 spans" in check.message and "tokens unread; floor 80%" in check.message


def test_the_dead_token_share_is_over_the_read_text_only():
    from dokimi.novelty.spans import span_summary

    spans, atoms, _report = _half_lost(silent=frozenset({49}))

    summary = span_summary(spans, atoms)
    read = sum(s.tokens for s in spans[25:])
    assert summary["tokens_carrying_nothing"] == spans[49].tokens
    # Over the whole text it would be half this: the unread half is not in the denominator.
    assert summary["frac_carrying_nothing"] == round(spans[49].tokens / read, 4)
    assert summary["frac_carrying_nothing"] != round(spans[49].tokens / summary["tokens"], 4)


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
