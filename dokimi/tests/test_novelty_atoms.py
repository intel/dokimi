# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Atom extraction under a truncating or failing extractor."""

from __future__ import annotations

import json
import re

import pytest

from dokimi.novelty.atoms import ExtractionError, ExtractionReport, extract_atoms, spread_sample
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


def test_a_capped_extraction_sends_every_batch():
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
    # All eight 25-span batches were asked, though the first two held 150 atoms: each cost
    # one cut call and two halves.
    assert (report.calls, report.batches, report.retried_batches) == (24, 8, 8)
    assert (
        "atom extraction re-asked 8 unparseable batches as halves: 24 extraction calls where "
        "8 would do without splitting; a higher --max-tokens avoids the extra calls"
    ) in report.warnings(len(spans))
    # The 120 kept are spread over all 600, one per 5, from the middle of the first stride.
    assert [a.span_ids[0] for a in atoms[:3]] == ["s0001", "s0003", "s0005"]
    assert atoms[-1].span_ids[0] == "s0200"


# The issue's root AGENTS.md: 61 atoms under the triage cap of 25.
SPREAD_61_25 = [1, 3, 6, 8, 10, 13, 15, 18, 20, 23, 25, 28, 30, 32, 35, 37, 40, 42, 45, 47,
                50, 52, 54, 57, 59]


@pytest.mark.parametrize("n_atoms, cap, kept", [
    (61, 25, SPREAD_61_25),
    (61, 1, [30]),             # one atom: the middle one, not the first
    (25, 25, list(range(25))),  # a cap the atoms meet exactly keeps them all
    (10, 25, list(range(10))),
    (10, None, list(range(10))),
], ids=["61-of-25", "cap-1", "at-cap", "under-cap", "no-cap"])
def test_a_cap_keeps_every_kth_atom_from_the_middle_of_the_first_stride(n_atoms, cap, kept):
    """``floor((i + 0.5) * M / n)``: the stride rule of https://github.com/intel/dokimi/issues/293."""
    assert spread_sample([f"atom{i}" for i in range(n_atoms)], cap) == [f"atom{i}" for i in kept]


def _cited(*span_ids: str) -> dict:
    return {"statement": f"{'+'.join(span_ids)} holds a default.", "question": f"q {span_ids}",
            "answer": "a value", "kind": "proposition", "tags": [], "span_ids": list(span_ids)}


@pytest.mark.parametrize("reply, cap, probed", [
    # Sorted s0001 s0002 s0003; the one stride's middle is s0002, where reply order gives s0003.
    ([("s0001",), ("s0003",), ("s0002",)], 1, [("s0002",)]),
    ([("s0001",), ("s0003",), ("s0002",), ("s0004",)], 2, [("s0002",), ("s0004",)]),
    # By the earliest span an atom cites, not its first: (s0004, s0001) sorts first.
    ([("s0003",), ("s0004", "s0001"), ("s0002",)], 1, [("s0002",)]),
    # A cap that does not bind keeps the reply's order: atoms.json is as before #293.
    ([("s0002",), ("s0001",)], 2, [("s0002",), ("s0001",)]),
    ([("s0001",), ("s0003",), ("s0002",)], None, [("s0001",), ("s0003",), ("s0002",)]),
], ids=["cap-1", "cap-2-of-4", "earliest-cited", "at-cap", "no-cap"])
def test_a_binding_cap_strides_in_document_order_whatever_the_reply_s_order(reply, cap, probed):
    def out_of_order(prompt, system):
        return json.dumps([_cited(*span_ids) for span_ids in reply]), None

    atoms = extract_atoms(segment(DOC), client=LLMClient(model="x", transport=out_of_order),
                          cap=cap)
    assert [atom.span_ids for atom in atoms] == probed


def test_a_cap_below_one_is_refused_before_any_call():
    calls: list[str] = []

    def counted(prompt, system):
        calls.append(prompt)
        return _three_per_span(prompt, system)

    with pytest.raises(ValueError, match="the atom cap must be at least 1"):
        extract_atoms(segment(DOC), client=LLMClient(model="x", transport=counted), cap=0)
    assert calls == []


def test_a_binding_cap_over_a_partly_read_input_makes_no_whole_input_claim():
    """50 spans, cap 2: the first batch gives 25 atoms and the second an empty reply."""
    from dokimi.novelty.report import _capped_at, cap_warning, coverage_reach

    spans = segment("\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 51)))
    second_batch = {s.id for s in spans[25:]}

    def second_empty(prompt, system):
        if second_batch & set(re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)):
            return "", None
        return json.dumps(_rows(prompt)), None

    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=second_empty), cap=2,
                          report=report)
    assert [a.span_ids[0] for a in atoms] == ["s0007", "s0019"]  # both from the opening
    assert report.sampled == {"atoms": 2, "of": 25, "rule": "cap-spread"}
    assert len(report.unread_span_ids) == 25
    record = report.as_dict()
    sample = ("2 of the 25 atoms extracted from the spans that were read, spread over those (a "
              "stride of 12.5 atoms in document order; 25 of the input's spans were not read, see "
              "extraction.unread_span_ids)")
    texts = [cap_warning(record), _capped_at({"extraction": record}), coverage_reach(record)[1]]
    assert texts == [
        f"atom extraction could not read 25 of the input's spans, and the atom cap probes {sample}: "
        "rates are estimated from that sample; pass --all-atoms to probe every atom",
        f" Extraction could not read 25 of the input's spans; the atom cap probed {sample}.",
        f"; the cap probed {sample}",
    ]
    assert not any("whole input" in text or "all of it" in text for text in texts)


def _three_per_span(prompt, system):
    rows = [dict(row, statement=f"{row['statement']} ({k})",
                 question=f"q{k} {row['span_ids'][0]}")
            for row in _rows(prompt) for k in range(3)]
    return json.dumps(rows), None


def test_a_capped_extraction_reads_every_span_and_records_the_sample(tmp_path):
    """60 spans, 3 atoms each, 10 per batch: a cap of 25 keeps 25 of 180, from every part."""
    from dokimi.novelty.atoms import save_atoms

    doc = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 61))
    spans = segment(doc)
    calls: list[str] = []

    def counted(prompt, system):
        calls.append(prompt)
        return _three_per_span(prompt, system)

    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=counted),
                          batch_spans=10, cap=25, report=report)
    assert (len(calls), report.batches, report.calls) == (6, 6, 6)
    assert len(atoms) == 25
    # Every batch gives sampled atoms, the first from s0002 (atom 3) and the last from s0059 (176).
    assert {(int(a.span_ids[0][1:]) - 1) // 10 for a in atoms} == set(range(6))
    assert atoms[0].span_ids[0] == "s0002" and atoms[-1].span_ids[0] == "s0059"
    assert report.sampled == {"atoms": 25, "of": 180, "rule": "cap-spread"}
    path = tmp_path / "atoms.json"
    save_atoms(atoms, spans, path, cap=25, extraction=report)
    saved = json.loads(path.read_text())["extraction"]
    assert (saved["sampled"], saved["reached"]) == (report.sampled, None)
    # Selection is a pure function of the extracted list: a second run picks the same atoms.
    again = extract_atoms(segment(doc), client=LLMClient(model="x", transport=_three_per_span),
                          batch_spans=10, cap=25)
    assert [a.id for a in again] == [a.id for a in atoms]


@pytest.mark.parametrize("cap", [None, 31, 30], ids=["no-cap", "above", "at-cap"])
def test_a_cap_the_atoms_do_not_exceed_keeps_them_all_and_records_no_sample(cap):
    """No cap, a cap above the 30 atoms, and a cap the last atom meets exactly."""
    spans = segment(DOC)
    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=_three_per_span),
                          cap=cap, report=report)
    assert len(atoms) == 30 and len(spans) == 10
    assert report.sampled is None
    assert (report.as_dict()["sampled"], report.as_dict()["reached"]) == (None, None)


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


def _replying(make, calls: list[int]):
    """A transport that answers every batch with ``make(rows)``, rows being the good ones."""

    def transport(prompt, system):
        rows = _rows(prompt)
        calls.append(len(rows))
        reply = make(rows)
        return (reply if isinstance(reply, str) else json.dumps(reply)), None

    return transport


def _renamed(rows):
    return [{"text": r["statement"], "q": r["question"], "ids": r["span_ids"]} for r in rows]


def _strings(rows):
    return [r["statement"] for r in rows]


def _zero_padded(rows):
    return [{**r, "span_ids": [r["span_ids"][0].replace("s", "", 1)]} for r in rows]


def _outside(rows):
    return [{**r, "span_ids": ["s9999"]} for r in rows]


def _no_span(rows):
    return [{k: v for k, v in r.items() if k != "span_ids"} for r in rows]


_UNUSABLE = [_renamed, _strings, _zero_padded, _outside, _no_span]


@pytest.mark.parametrize("make", _UNUSABLE)
def test_a_reply_whose_rows_are_all_unusable_is_not_an_empty_batch(make):
    spans = segment(DOC)
    calls: list[int] = []
    report = ExtractionReport()
    client = LLMClient(model="x", transport=_replying(make, calls))
    with pytest.raises(ExtractionError):
        extract_atoms(spans, client=client, batch_spans=10, report=report)
    # Split like any reply that carries nothing readable, down to one span per call.
    assert len(calls) == report.calls == 2 * len(spans) - 1
    assert report.unread_span_ids == report.unusable_span_ids == [s.id for s in spans]
    assert report.unparseable_span_ids == [] and all(s.unread for s in spans)


def test_unusable_rows_are_named_apart_from_a_cut_off_reply():
    spans = segment(DOC)
    bad = spans[4].id
    report = ExtractionReport()

    def transport(prompt, system):
        rows = _rows(prompt)
        hit = bad in {r["span_ids"][0] for r in rows}
        return json.dumps(_renamed(rows) if hit else rows), None

    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=transport),
                          report=report)
    assert [a.span_ids[0] for a in atoms] == [s.id for s in spans if s.id != bad]
    assert [s.id for s in spans if s.unread] == [bad] == report.unusable_span_ids
    [lost, _] = report.warnings(len(spans))
    assert lost.startswith(f"atom extraction could not read 1 of {len(spans)} spans")
    assert "rows but none usable" in lost and "output ceiling" not in lost
    assert "failed or came back empty" not in lost
    assert report.as_dict()["unusable_span_ids"] == [bad]


@pytest.mark.parametrize("reply", ["[]", '{"atoms": []}', '{"atoms": [], "note": "boilerplate"}'])
def test_a_legitimate_empty_reply_is_a_result_not_split_or_unread(reply):
    spans = segment(DOC)
    calls: list[int] = []
    report = ExtractionReport()
    client = LLMClient(model="x", transport=_replying(lambda rows: reply, calls))
    assert extract_atoms(spans, client=client, batch_spans=10, report=report) == []
    assert calls == [10] and report.retried_batches == 0
    assert report.unread_span_ids == [] and not any(s.unread for s in spans)
    assert report.warnings(len(spans)) == []


def test_a_reply_with_one_usable_row_among_unusable_ones_is_kept_whole():
    spans = segment(DOC)
    calls: list[int] = []

    def mixed(rows):
        return json.dumps(["junk", {"statement": "no question"}, *rows[:1]])

    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(model="x", transport=_replying(mixed, calls)),
                          batch_spans=10, report=report)
    assert [a.span_ids[0] for a in atoms] == [spans[0].id] and len(calls) == 1
    assert report.unread_span_ids == []


def test_a_healthy_extractor_after_a_useless_reply_still_reads_everything():
    spans = segment(DOC)
    with pytest.raises(ExtractionError):
        extract_atoms(spans, client=LLMClient(
            model="x", transport=_replying(_renamed, [])), batch_spans=10)
    assert all(s.unread for s in spans)
    report = ExtractionReport()
    atoms = extract_atoms(spans, client=LLMClient(
        model="x", transport=_replying(json.dumps, [])), batch_spans=10, report=report)
    assert len(atoms) == len(spans) and not any(s.unread for s in spans)
    assert report.unread_span_ids == []


def _mixed_causes(causes: list[str]):
    """Two spans per batch; batch i fails the way ``causes[i]`` says, the rest are healthy."""
    spans = segment(DOC)
    batch_of = {s.id: i // 2 for i, s in enumerate(spans)}

    def transport(prompt, system):
        rows = _rows(prompt)
        cause = causes[batch_of[rows[0]["span_ids"][0]]] if rows else ""
        if cause == "empty":
            return "", None
        if cause == "unusable":
            return json.dumps(_renamed(rows)), None
        if cause == "unparseable":
            return json.dumps(rows)[:20], None
        if cause == "call":
            raise RuntimeError("RateLimitError: slow down")
        return json.dumps(rows), None

    report = ExtractionReport()
    extract_atoms(spans, client=LLMClient(model="x", transport=transport),
                  batch_spans=2, report=report)
    return spans, report


def test_a_span_is_counted_under_one_unread_cause_only():
    spans, report = _mixed_causes(["empty", "unusable", "unparseable", "call", "ok"])
    causes = [report.empty_span_ids, report.unusable_span_ids, report.unparseable_span_ids]
    assert [len(c) for c in causes] == [2, 2, 2]
    named = [sid for c in causes for sid in c]
    assert len(named) == len(set(named))  # no span under two causes
    assert report.unread_span_ids == [s.id for s in spans[:8]]
    [lost, _] = report.warnings(len(spans))
    assert lost.startswith(f"atom extraction could not read 8 of {len(spans)} spans")
    assert lost.count(" replied but did not parse") == 1 and "2 got an empty reply" in lost
    assert "2 replied with rows but none usable" in lost
    assert "2 were lost to calls that failed" in lost  # the call failures, and only those


@pytest.mark.parametrize(("causes", "first"), [
    (["empty", "unusable", "unparseable", "call", "ok"], "RuntimeError: RateLimitError"),
    (["empty", "unusable", "unparseable", "ok", "ok"], "ValueError"),
    (["empty", "unusable", "ok", "ok", "ok"], "1 rows, none an atom of this document"),
    (["empty", "ok", "ok", "ok", "ok"], "the extractor returned an empty reply"),
])
def test_the_last_error_names_a_call_then_a_parse_then_an_unusable_then_an_empty_cause(
        causes, first):
    _spans, report = _mixed_causes(causes)
    assert report.last_error.startswith(first)
    assert "_NoUsableRows" not in report.last_error


def _junk_for(bad: frozenset[str]):
    def transport(prompt, system):
        rows = _rows(prompt)
        if bad & {r["span_ids"][0] for r in rows}:
            return json.dumps([{"x": 1}] * 3), None  # parses, holds no atom
        return json.dumps(rows), None

    return transport


def test_a_split_for_unusable_rows_does_not_blame_the_output_ceiling():
    spans = segment(DOC)
    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor", transport=_junk_for(frozenset({"s0001"})))
    extract_atoms(spans, client=client, batch_spans=5, report=report)
    assert report.unparseable_span_ids == [] and report.unusable_span_ids == ["s0001"]
    assert report.retried_batches > 0 and report.unusable_retries == report.retried_batches
    resplit = [w for w in report.warnings(len(spans)) if "as halves" in w]
    assert resplit == [
        f"atom extraction re-asked {report.retried_batches} unusable batches as halves: "
        f"{report.calls} extraction calls where {report.batches} would do without splitting"
    ]


def test_a_run_with_cut_off_and_unusable_splits_names_both_and_keeps_the_advice():
    spans = segment(DOC)
    report = ExtractionReport()
    cut = _ceiling(3, [], frozenset({"s0002"}))
    junk = _junk_for(frozenset({"s0001"}))

    def transport(prompt, system):
        return (junk if "[s0001]" in prompt else cut)(prompt, system)

    client = LLMClient(model="x", role="extractor", transport=transport)
    extract_atoms(spans, client=client, batch_spans=10, report=report)
    assert 0 < report.unusable_retries < report.retried_batches
    assert any(
        "unparseable or unusable batches as halves" in w and w.endswith("avoids the extra calls")
        for w in report.warnings(len(spans))
    )


def test_novelty_atoms_saves_the_same_spread_sample_analyze_probes(tmp_path, monkeypatch, capsys):
    """Two batches of 25 spans, one atom each: a cap of 2 keeps s0013 and s0038 for probe and shorten."""
    from tests import test_novelty_cli as novelty_cli

    skill = tmp_path / "s" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: s\ndescription: d\n---\n\n" + "\n\n".join(
        f"Setting {i} defaults to {i}." for i in range(1, 51)) + "\n", encoding="utf-8")
    batches: list = []

    def transport(prompt, system=None):
        ids = re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)
        batches.append(ids)
        return json.dumps([{"statement": f"{sid} holds.", "question": f"What of {sid}?",
                            "answer": "a value", "span_ids": [sid]} for sid in ids]), None

    real_client = novelty_cli.novelty_cmd._client
    monkeypatch.setattr(novelty_cli.novelty_cmd, "_client",
                        lambda model, provider, role, args: novelty_cli._with_transport(
                            real_client(model, provider, role, args), transport))
    out = tmp_path / "atoms.json"
    args = novelty_cli._parse("novelty", "atoms", "--artifact", f"skill:{skill}", "--out",
                              str(out), "--max-atoms", "2", "--no-verify")
    args.func(args)
    payload = json.loads(out.read_text())
    assert len(batches) == 2  # the cap no longer stops extraction after the first batch
    assert [atom["span_ids"] for atom in payload["atoms"]] == [["s0013"], ["s0038"]]
    assert payload["extraction"]["sampled"] == {"atoms": 2, "of": 50, "rule": "cap-spread"}
    assert ("WARNING atom extraction read the whole input, and the atom cap probes 2 of the "
            "input's 50 atoms") in capsys.readouterr().err
