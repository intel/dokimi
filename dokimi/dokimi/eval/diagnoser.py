# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Failure diagnosis for documentation benchmark evaluations.

For each evaluated question, determines *why* the context-arm answer
scored differently from the baseline answer.

Diagnosis categories
--------------------
docs_helped
    delta >= 0. Documentation improved or matched the baseline.

empty_retrieval
    No documentation was retrieved at all (MCP returned nothing,
    or the source was unavailable).

below_threshold
    Docs were fetched but every chunk scored below the reranker
    threshold — none were passed to the LLM.

low_relevance
    Docs were passed to the LLM but had low keyword-overlap scores
    (top_score < LOW_RELEVANCE_CUTOFF). The retrieved content was
    likely off-topic.

knowledge_sufficient
    Docs were retrieved with reasonable relevance, but the model
    already knows enough to answer without them.  The documentation
    added noise rather than signal.

insufficient_data
    Delta could not be computed (one or both answers missing).
"""

from typing import Dict, Any, Optional

# Keyword-overlap score below which retrieved docs are considered off-topic
LOW_RELEVANCE_CUTOFF = 0.3

# ``doc_source`` values the answerer writes when no document reached the prompt
NO_RETRIEVAL_SOURCES = ("fallback_none", "none")


def retrieval_hit(answer: Dict[str, Any]) -> Optional[bool]:
    """Whether retrieval put at least one document into the context-arm prompt.

    Derived from what ``Answerer`` records on the answers.v1 record: the
    ``retrieved_docs`` it passed to the model and the ``doc_source`` it stamps
    (``fallback_none`` when retrieval or reranking returned nothing). ``None``
    when the record has no context arm, so there was no retrieval to score.
    """
    with_docs = answer.get("with_docs")
    if not isinstance(with_docs, dict):
        return None
    if with_docs.get("doc_source") in NO_RETRIEVAL_SOURCES:
        return False
    return bool(with_docs.get("retrieved_docs"))

# Diagnosis labels (exported for reporting)
DOCS_HELPED = "docs_helped"
EMPTY_RETRIEVAL = "empty_retrieval"
BELOW_THRESHOLD = "below_threshold"  # reserved — not yet emitted by diagnose()
LOW_RELEVANCE = "low_relevance"
KNOWLEDGE_SUFFICIENT = "knowledge_sufficient"
INSUFFICIENT_DATA = "insufficient_data"

# Human-readable labels for reports
DIAGNOSIS_LABELS: Dict[str, str] = {
    DOCS_HELPED: "✅ Docs helped",
    EMPTY_RETRIEVAL: "🔴 Empty retrieval",
    BELOW_THRESHOLD: "🟠 Below rerank threshold",
    LOW_RELEVANCE: "🟡 Low relevance",
    KNOWLEDGE_SUFFICIENT: "🔵 Model knowledge sufficient",
    INSUFFICIENT_DATA: "⚪ Insufficient data",
}

# Brief explanation shown in reports
DIAGNOSIS_DESCRIPTIONS: Dict[str, str] = {
    DOCS_HELPED: "Documentation improved or matched the baseline answer.",
    EMPTY_RETRIEVAL: "No documentation was retrieved from the source.",
    BELOW_THRESHOLD: "Docs were fetched but all chunks scored below the rerank threshold.",
    LOW_RELEVANCE: "Retrieved docs had low relevance scores — likely off-topic content.",
    KNOWLEDGE_SUFFICIENT: "Docs were retrieved but the model already knew the answer.",
    INSUFFICIENT_DATA: "Delta could not be computed (answer missing or errored).",
}


def diagnose(
    answer: Dict[str, Any],
    eval_result: Dict[str, Any],
    low_relevance_cutoff: float = LOW_RELEVANCE_CUTOFF,
) -> Dict[str, Any]:
    """
    Classify the retrieval outcome for a single question.

    Parameters
    ----------
    answer:
        The answer dict produced by ``Answerer.generate_answers()`` for one
        question (contains ``with_docs`` and ``without_docs`` sub-dicts).
    eval_result:
        The evaluation dict produced by ``Judge._evaluate_answer_pair()``
        (contains ``delta``, ``with_docs``, ``without_docs``).
    low_relevance_cutoff:
        Relevance score below which retrieved docs are treated as off-topic.

    Returns
    -------
    dict with keys:
        - ``label``  : diagnosis constant (e.g. ``"empty_retrieval"``)
        - ``detail`` : human-readable description
        - ``evidence``: dict of signals used to reach the diagnosis
    """
    delta = eval_result.get("delta")

    if delta is None:
        return _result(INSUFFICIENT_DATA, {})

    if delta >= 0:
        return _result(DOCS_HELPED, {"delta": delta})

    # delta < 0 — classify the failure
    with_docs_answer = (answer.get("with_docs") or {})
    doc_source = with_docs_answer.get("doc_source", "")
    retrieved_docs = with_docs_answer.get("retrieved_docs") or []

    evidence: Dict[str, Any] = {
        "delta": round(delta, 2),
        "doc_source": doc_source,
        "retrieved_count": len(retrieved_docs),
    }

    # 1. Nothing retrieved at all
    if doc_source in ("fallback_none", "none", "") or not retrieved_docs:
        # Distinguish: was source unavailable vs docs below threshold?
        if doc_source == "none":
            return _result(EMPTY_RETRIEVAL, {**evidence, "reason": "no_mcp_client"})
        return _result(EMPTY_RETRIEVAL, evidence)

    # 2. Docs retrieved — examine relevance scores
    scores = [
        d["relevance_score"]
        for d in retrieved_docs
        if d.get("relevance_score") is not None
    ]

    if scores:
        top_score = max(scores)
        avg_score = sum(scores) / len(scores)
        evidence["top_relevance_score"] = round(top_score, 3)
        evidence["avg_relevance_score"] = round(avg_score, 3)

        if top_score < low_relevance_cutoff:
            return _result(LOW_RELEVANCE, evidence)
    else:
        evidence["scores_available"] = False

    # 3. Docs were relevant — model already knew the answer
    return _result(KNOWLEDGE_SUFFICIENT, evidence)


def summarise_diagnoses(evaluations: list) -> Dict[str, Any]:
    """
    Aggregate diagnosis statistics across all evaluations.

    Parameters
    ----------
    evaluations:
        List of eval dicts (each may or may not have a ``diagnosis`` key).

    Returns
    -------
    dict with:
        - ``counts``  : {label: int}
        - ``rates``   : {label: float}  (0-1)
        - ``total``   : int
        - ``failures``: list of eval dicts where delta < 0
    """
    total = len(evaluations)
    counts: Dict[str, int] = {k: 0 for k in DIAGNOSIS_LABELS}

    failures = []
    for e in evaluations:
        diag = (e.get("diagnosis") or {}).get("label", INSUFFICIENT_DATA)
        counts[diag] = counts.get(diag, 0) + 1
        if (e.get("delta") or 0) < 0:
            failures.append(e)

    rates = {k: round(v / total, 3) if total else 0.0 for k, v in counts.items()}
    return {
        "total": total,
        "counts": counts,
        "rates": rates,
        "failures": failures,
    }


def _result(label: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "label": label,
        "detail": DIAGNOSIS_DESCRIPTIONS[label],
        "evidence": evidence,
    }
