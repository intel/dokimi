# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A reply cut at the output ceiling is an error, and novelty calls ask for a real ceiling."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from dokimi.metrics.usage import UsageRecord
from dokimi.novelty import batching
from dokimi.novelty.atoms import ExtractionError, ExtractionReport, extract_atoms
from dokimi.novelty.client import DEFAULT_MAX_TOKENS, LLMClient, OutputTruncated
from dokimi.novelty.spans import segment

DOC = "\n\n".join(f"Setting number {i} defaults to {i * 10}." for i in range(1, 11))


def _provider(monkeypatch, finish_reason: str, calls: list[dict]):
    """A litellm stub: every call returns a reply with ``finish_reason``, and is recorded."""
    import litellm

    def completion(model, messages, api_key=None, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='[{"a": 1'),
                                     finish_reason=finish_reason)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4096),
        )

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("DOKIMI_RESPONSE_CACHE", raising=False)
    monkeypatch.setattr(litellm, "completion", completion)
    return calls


@pytest.mark.parametrize("finish_reason, cut", [("length", True), ("max_tokens", True),
                                                ("stop", False)])
def test_a_reply_cut_at_the_ceiling_is_marked_on_its_usage(monkeypatch, finish_reason, cut):
    from dokimi import llm

    _provider(monkeypatch, finish_reason, [])
    usage = llm.llm_call_with_usage("p", model="m", provider="openai")[1]
    assert usage.truncated is cut


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens"])
def test_a_client_call_cut_at_the_ceiling_raises_and_still_records_its_cost(monkeypatch,
                                                                            finish_reason):
    _provider(monkeypatch, finish_reason, [])
    client = LLMClient(model="m", provider="openai", role="judge")
    with pytest.raises(OutputTruncated, match="raise --max-tokens"):
        client.text("p")
    assert client.calls == 1 and len(client.usage) == 1


def test_a_cached_cut_reply_still_raises_on_replay(monkeypatch, tmp_path):
    calls = _provider(monkeypatch, "length", [])
    monkeypatch.setenv("DOKIMI_RESPONSE_CACHE", str(tmp_path))
    for _ in range(2):
        with pytest.raises(OutputTruncated):
            LLMClient(model="m", provider="openai").text("p")
    assert len(calls) == 1  # the second run replays the stored reply, and still knows it was cut


def test_a_novelty_call_asks_for_the_default_ceiling_when_none_is_named(monkeypatch):
    calls = _provider(monkeypatch, "stop", [])
    LLMClient(model="m", provider="openai").text("p")
    assert calls[0]["max_tokens"] == DEFAULT_MAX_TOKENS


def test_an_explicit_ceiling_is_sent_as_given(monkeypatch):
    calls = _provider(monkeypatch, "stop", [])
    LLMClient(model="m", provider="openai", max_tokens=2048).text("p")
    assert calls[0]["max_tokens"] == 2048


def _rows(prompt: str) -> list[dict]:
    return [
        {"statement": f"{sid} holds a default.", "question": f"What is the default of {sid}?",
         "answer": "a value", "kind": "proposition", "tags": ["local"], "span_ids": [sid]}
        for sid in re.findall(r"^\[(s\d+)\]", prompt, flags=re.M)
    ]


def _cut_above(max_spans: int):
    """A transport whose reply is cut (usage says so) when it carries more than ``max_spans`` rows."""

    def transport(prompt, system):
        rows = _rows(prompt)
        return json.dumps(rows), UsageRecord(truncated=len(rows) > max_spans)

    return transport


def test_a_batch_cut_at_the_ceiling_is_split_and_every_span_still_yields_an_atom():
    spans = segment(DOC)
    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor", transport=_cut_above(3))
    atoms = extract_atoms(spans, client=client, batch_spans=10, report=report)
    assert {a.span_ids[0] for a in atoms} == {s.id for s in spans}
    assert report.retried_batches > 0 and report.parse_error == ""


def test_a_span_cut_even_alone_is_named_as_truncated_not_as_a_parse_failure():
    spans = segment(DOC)
    report = ExtractionReport()
    client = LLMClient(model="x", role="extractor", transport=_cut_above(0))
    with pytest.raises(ExtractionError, match="raise --max-tokens"):
        extract_atoms(spans, client=client, batch_spans=1, report=report)
    assert report.parse_error.startswith("OutputTruncated")
    assert report.unparseable_span_ids == [s.id for s in spans]


def test_ask_in_batches_splits_a_chunk_cut_at_the_ceiling():
    def transport(prompt, system):
        items = [int(m) for m in re.findall(r"^item (\d+)$", prompt, flags=re.M)]
        return json.dumps(items), UsageRecord(truncated=len(items) > 2)

    items = list(range(8))
    result = batching.ask_in_batches(
        LLMClient(model="x", transport=transport), items, size=8, parse=json.loads,
        prompt=lambda chunk: "\n".join(f"item {i}" for i in chunk),
    )
    assert [i for chunk, _ in result.answered for i in chunk] == items
    assert result.failed == [] and result.retried_batches > 0


def test_a_routing_pick_cut_at_the_ceiling_is_an_abstention_not_an_error():
    from dokimi.novelty.routing import Candidate, model_routing

    cards = [Candidate(name="a", description="alpha tool"), Candidate(name="b", description="beta tool")]

    def transport(prompt, system):
        return '{"pick": 1', UsageRecord(truncated=True)

    result = model_routing(cards, ["alpha"], client=LLMClient(model="x", transport=transport))
    assert result["n_abstained"] == 1 and result["n_failed"] == 0


def test_an_empty_reply_cut_at_the_ceiling_is_not_split_it_fails_as_before():
    # A reasoning model that spends the whole ceiling returns no text: halving the chunk
    # cannot give it text, so the chunk fails on its first call, as an empty reply does.
    calls = []

    def transport(prompt, system):
        calls.append(prompt)
        return "", UsageRecord(truncated=True)

    result = batching.ask_in_batches(
        LLMClient(model="x", transport=transport), list(range(8)), size=8, parse=json.loads,
        prompt=lambda chunk: "\n".join(f"item {i}" for i in chunk),
    )
    assert len(calls) == 1 and result.retried_batches == 0 and result.failed


def test_a_routing_cut_pick_keeps_the_start_of_its_reply_for_the_report():
    from dokimi.novelty.routing import Candidate, model_routing

    cards = [Candidate(name="a", description="alpha tool"), Candidate(name="b", description="beta tool")]
    cut = '{"pick": 1, "why": "alpha is'

    def transport(prompt, system):
        return cut, UsageRecord(truncated=True)

    result = model_routing(cards, ["alpha"], client=LLMClient(model="x", transport=transport))
    assert result["per_query"][0]["raw_reply"] == cut
