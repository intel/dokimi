# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The partial marker of a routing result cut short by ``--max-usd``, and the repeat noise
of a model arm. Split from ``routing.py``, which stays under its size cap."""

from __future__ import annotations

from collections import Counter


def partial_record(message: str, calls_done: int, calls_planned: int) -> dict:
    """The marker of a result cut short by ``--max-usd``: what ran, of what was planned."""
    return {"reason": "spend_cap", "calls_done": calls_done, "calls_planned": calls_planned,
            "message": message}


def repeat_noise(groups: list[list[dict]]) -> dict:
    """Agreement across the repeats of each query, and the run-to-run spread of the rate.

    A query's agreement is the share of its answered repeats that gave its most common
    pick; an abstention counts as a pick. ``selection_rate_by_repeat`` reads each repeat as
    its own run over the same queries, and ``half_range`` is half the gap between the
    highest and the lowest of those rates. It is a crude sampling noise floor: a few repeats
    estimate it coarsely, and it says nothing about a change of cards or queries. The
    per-repeat rates cover only the queries with no failed call in any repeat
    (``n_queries_compared``), so a transient error does not read as noise.

    Args:
        groups: One list per query, holding that query's rows in repeat order.
    """
    per_query: list[dict] = []
    for group in groups:
        labels = [
            "(abstained)" if row.get("abstained") else row["picked"]
            for row in group if not row.get("error")
        ]
        modal, agreement = None, None
        if labels:
            modal, count = Counter(labels).most_common(1)[0]
            agreement = round(count / len(labels), 4)
        per_query.append({
            "query": group[0]["query"],
            "modal_pick": modal,
            "agreement": agreement,
            "picks": labels,
            "n_correct": sum(1 for row in group if row.get("correct")),
        })
    repeats = len(groups[0]) if groups else 0
    rates: list[float | None] = []
    # Every repeat's rate covers the same queries, those with no failed call in any repeat: a
    # per-repeat denominator would report a transient provider error as sampling noise.
    complete = [group for group in groups if not any(row.get("error") for row in group)]
    for repeat in range(repeats):
        rates.append(round(sum(1 for g in complete if g[repeat].get("correct")) / len(complete), 4)
                     if complete else None)
    known = [rate for rate in rates if rate is not None]
    agreements = [row["agreement"] for row in per_query if row["agreement"] is not None]
    return {
        "mean_agreement": round(sum(agreements) / len(agreements), 4) if agreements else None,
        "unstable_queries": sum(1 for a in agreements if a < 1),
        "selection_rate_by_repeat": rates,
        "n_queries_compared": len(complete),
        "half_range": round((max(known) - min(known)) / 2, 4) if len(known) > 1 else None,
        "per_query": per_query,
    }
