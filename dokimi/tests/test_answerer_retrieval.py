# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Stack P's Answerer retrieves through DocTreatment, Stack A's path (#228 step 3).

For one config (top_k, retrieval budget, reranker) both send the same
``get_library_docs`` request and keep the same docs, also when the reranker
raises or drops every doc.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from dokimi.eval import answerer as answerer_mod
from dokimi.eval.answerer import Answerer
from dokimi.eval.reranker import SimpleReranker
from dokimi.treatments.arms import DocTreatment

LIBRARY_ID = "uxlfoundation/oneTBB"
QUESTION = "How does parallel_for split a blocked_range into subranges?"


class RecordingClient:
    """A doc source that records the keywords of each request and returns fixed chunks."""

    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.docs = DOCS if docs is None else docs

    def get_library_docs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        self.calls.append(kwargs)
        return [dict(d) for d in self.docs]


DOCS = [
    {"source": "a.md", "content": "parallel_for splits a blocked_range into subranges recursively."},
    {"source": "b.md", "content": "The task_arena class limits concurrency."},
    {"source": "c.md", "content": "blocked_range subranges are split until the grain size; parallel_for runs each."},
    {"source": "d.md", "content": "How parallel_for splits a blocked_range into subranges: each subrange runs the body."},
    {"source": "e.md", "content": "Unrelated text about flow graphs."},
    {"source": "f.md", "content": "parallel_for splits blocked_range into subranges until grain size."},
]


class RaisingReranker:
    def rerank(self, question, docs):
        raise RuntimeError("reranker unavailable")


class DropAllReranker:
    def rerank(self, question, docs):
        return []


@pytest.fixture(autouse=True)
def _lexical_reranker(monkeypatch):
    """The Answerer builds the lexical reranker, so both sides rank the same way without a model download."""
    monkeypatch.setattr(answerer_mod, "SENTENCE_TRANSFORMERS_AVAILABLE", False)
    monkeypatch.setattr(answerer_mod, "LANGCHAIN_AVAILABLE", True)


def _stack_p(top_k, retrieval_tokens, reranker=None, docs=None):
    client = RecordingClient(docs)
    answerer = Answerer(mcp_client=client, model="m", provider="openai", top_k=top_k, rerank_threshold=0.3)
    if reranker is not None:
        answerer.reranker = reranker
    kept, meta = answerer._retrieve_docs(LIBRARY_ID, QUESTION, retrieval_tokens, return_metadata=True)
    return client.calls, [d["source"] for d in kept], meta


def _stack_a(top_k, retrieval_tokens, reranker=None, docs=None):
    client = RecordingClient(docs)
    arm = DocTreatment(client, name="docs", top_k=top_k, retrieval_tokens=retrieval_tokens,
                       reranker=reranker or SimpleReranker(threshold=0.3))
    cfg = arm.prepare(QUESTION, "oneTBB", library_id=LIBRARY_ID)
    return client.calls, [d["source"] for d in cfg.injected_context], cfg.metadata


@pytest.mark.parametrize("top_k,retrieval_tokens", [(3, 4000), (5, 1200)])
def test_answerer_and_doc_treatment_send_one_request_and_keep_the_same_docs(top_k, retrieval_tokens):
    p_calls, p_kept, p_meta = _stack_p(top_k, retrieval_tokens)
    a_calls, a_kept, a_meta = _stack_a(top_k, retrieval_tokens)

    assert p_calls == a_calls == [
        {"library_id": LIBRARY_ID, "query": QUESTION, "max_results": top_k, "max_tokens": retrieval_tokens}
    ]
    assert p_kept == a_kept
    # The lexical reranker keeps the four on-topic chunks of six; both stacks answer from the best three.
    assert (p_meta["after_rerank"], a_meta["after_rerank"]) == (4, 4)
    assert len(p_kept) == 3 and p_kept[0] == "d.md"


def test_doc_treatment_without_a_budget_leaves_the_client_default():
    """Stack A's arms set no retrieval budget, so their request is the one they sent before."""
    calls, _, _ = _stack_a(5, None)
    assert calls == [{"library_id": LIBRARY_ID, "query": QUESTION, "max_results": 5}]


def test_a_rerank_failure_keeps_the_raw_docs_on_both_stacks():
    """#228 D3: the shared path answers from the first raw docs, not with the no-docs placeholder."""
    _, p_kept, p_meta = _stack_p(5, 4000, reranker=RaisingReranker())
    _, a_kept, a_meta = _stack_a(5, 4000, reranker=RaisingReranker())

    assert p_kept == a_kept == ["a.md", "b.md", "c.md"]
    assert p_meta["error"] == a_meta["error"] == "rerank_failed"


@pytest.mark.parametrize("docs", [DOCS, DOCS[:1]], ids=["six", "one"])
def test_debug_metadata_keeps_the_stack_p_counts_and_flags_an_empty_rerank(docs, caplog):
    _, kept, meta = _stack_p(5, 4000, reranker=DropAllReranker(), docs=docs)

    assert kept == []
    assert {k: meta[k] for k in ("raw_count", "after_rerank", "top_score", "fallback_triggered")} == {
        "raw_count": len(docs), "after_rerank": 0, "top_score": None, "fallback_triggered": True,
    }
    assert "No docs passed rerank threshold" in caplog.text


def test_debug_metadata_does_not_flag_a_doc_source_with_nothing(caplog):
    _, kept, meta = _stack_p(5, 4000, docs=[])

    assert kept == []
    assert (meta["raw_count"], meta["fallback_triggered"]) == (0, False)
    assert "No docs passed rerank threshold" not in caplog.text


def test_a_retrieval_failure_is_no_docs_with_the_reason():
    class Down(RecordingClient):
        def get_library_docs(self, **kwargs):
            raise ConnectionError("doc source down")

    answerer = Answerer(mcp_client=Down(), model="m", provider="openai")
    kept, meta = answerer._retrieve_docs(LIBRARY_ID, QUESTION, 4000, return_metadata=True)

    assert kept == []
    assert (meta["error"], meta["fallback_triggered"]) == ("retrieval_failed", False)
