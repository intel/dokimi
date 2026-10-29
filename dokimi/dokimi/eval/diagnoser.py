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
    Delta could not be computed (one or both answers missing or errored). A
    retrieval miss that was left unjudged is ``empty_retrieval``, not this.
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional

from dokimi.records import NO_RETRIEVAL_SOURCES, Answer, RecordLike, records

# Keyword-overlap score below which retrieved docs are considered off-topic
LOW_RELEVANCE_CUTOFF = 0.3

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
    context_answer: Optional[Answer],
    delta: Optional[float],
    low_relevance_cutoff: float = LOW_RELEVANCE_CUTOFF,
) -> Dict[str, Any]:
    """
    Classify the retrieval outcome for a single question.

    Parameters
    ----------
    context_answer:
        The question's context-arm answer (``Record.answer(CONTEXT)`` of the
        answers record), ``None`` when the context arm produced none.
    delta:
        Context-arm score minus baseline score, ``None`` when either is missing.
    low_relevance_cutoff:
        Relevance score below which retrieved docs are treated as off-topic.

    Returns
    -------
    dict with keys:
        - ``label``  : diagnosis constant (e.g. ``"empty_retrieval"``)
        - ``detail`` : human-readable description
        - ``evidence``: dict of signals used to reach the diagnosis
    """
    if delta is None:
        # A retrieval miss is left unjudged, so it has no delta; it is still an
        # empty retrieval, not a missing or errored answer.
        if context_answer is not None and not context_answer.error and context_answer.doc_source in NO_RETRIEVAL_SOURCES:
            return _result(EMPTY_RETRIEVAL, {"doc_source": context_answer.doc_source, "retrieved_count": 0})
        return _result(INSUFFICIENT_DATA, {})

    if delta >= 0:
        return _result(DOCS_HELPED, {"delta": delta})

    # delta < 0 — classify the failure
    doc_source = (context_answer.doc_source if context_answer else None) or ""
    retrieved_docs = context_answer.docs if context_answer else ()

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
    scores = [d.relevance for d in retrieved_docs if d.relevance is not None]

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


def summarise_diagnoses(evaluations: Iterable[RecordLike]) -> Dict[str, Any]:
    """
    Aggregate diagnosis statistics across all evaluations.

    Parameters
    ----------
    evaluations:
        Eval records (each may or may not carry a diagnosis).

    Returns
    -------
    dict with:
        - ``counts``  : {label: int}
        - ``rates``   : {label: float}  (0-1)
        - ``total``   : int
        - ``failures``: the persisted eval records whose ``delta`` is below 0
    """
    recs = records(evaluations, kind="evaluations")
    total = len(recs)
    counts: Dict[str, int] = {k: 0 for k in DIAGNOSIS_LABELS}

    failures: List[Mapping[str, Any]] = []
    for r in recs:
        diag = (r.diagnosis or {}).get("label", INSUFFICIENT_DATA)
        counts[diag] = counts.get(diag, 0) + 1
        if (r.delta or 0) < 0:
            failures.append(r.raw)

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
