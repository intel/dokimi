# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Shortening candidates: five axes, and the protection axis as a veto."""

from __future__ import annotations

import json

from dokimi.novelty.atoms import Atom
from dokimi.novelty.client import LLMClient
from dokimi.novelty.retention import (
    ABSENT,
    RETAINED,
    RetentionOracle,
    lexical_overlap,
)
from dokimi.novelty.shorten import build_candidates, pick_best, render
from dokimi.novelty.spans import index_atoms, segment

DOC = """\
# Tuning

Set the thread count to the core count. Do not set it above the core count.

The scheduler is described below. The scheduler is described below as well.

## Limits

Never exceed 80% memory utilization on version 3.1 or newer.
"""


def _prepared():
    spans = segment(DOC)
    atoms = []
    # Statements stay close to the source wording so the lexical oracle can resolve them
    # without an adjudicator; paraphrase behaviour is covered separately.
    for text, statement in (
        ("Set the thread count to the core count.", "Set the thread count to the core count."),
        ("Do not set it above the core count.", "Do not set it above the core count."),
        (
            "Never exceed 80% memory utilization on version 3.1 or newer.",
            "Never exceed 80% memory utilization on version 3.1 or newer.",
        ),
    ):
        span = next(s for s in spans if text in s.text)
        atoms.append(Atom(
            statement=statement, question=f"Q: {statement}", answer="see statement",
            span_ids=(span.id,),
        ))
    index_atoms(spans, atoms)
    return spans, atoms


def test_lexical_oracle_is_deterministic_and_bounded():
    assert lexical_overlap("thread count equals core count", "set thread count to core count") > 0.5
    assert lexical_overlap("nothing alike here", "completely different words") == 0.0


def test_oracle_without_an_adjudicator_resolves_the_ambiguous_band_to_absent():
    oracle = RetentionOracle(client=None)
    atom = Atom(statement="alpha beta gamma delta", question="q", answer="a", span_ids=("s1",))
    assert oracle.verdict(atom, "alpha beta gamma delta").retained
    verdict = oracle.verdict(atom, "alpha beta epsilon zeta")
    assert verdict.verdict == ABSENT
    assert verdict.method == "lexical"
    assert oracle.escalations == 0


def test_lexical_only_knowledge_is_a_floor_not_a_verdict():
    """A paraphrase sits in the ambiguous band, so a lexical-only run under-reports."""
    oracle = RetentionOracle(client=None)
    atom = Atom(
        statement="Thread count equals core count.", question="q", answer="a", span_ids=("s1",)
    )
    kept = "Set the thread count to the core count."
    assert not oracle.verdict(atom, kept).retained          # 0.75 overlap, band unresolved
    assert oracle.verdict(atom, kept).verdict == ABSENT
    with_llm = RetentionOracle(client=LLMClient(
        model="m", transport=lambda prompt, system: (json.dumps({"verdict": RETAINED}), None)
    ))
    assert with_llm.verdict(atom, kept).retained
    assert with_llm.escalations == 1


def test_safe_strategy_keeps_every_protected_span():
    spans, atoms = _prepared()
    candidates = build_candidates(
        spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.5,)
    )
    safe = [c for c in candidates if c.strategy == "greedy_value_safe"]
    assert safe and all(c.protection == 1.0 for c in safe)
    assert all(c.dropped_protected_spans == [] for c in safe)


def test_an_unsafe_candidate_is_reported_but_never_picked():
    spans, atoms = _prepared()
    candidates = build_candidates(
        spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.2,)
    )
    greedy = [c for c in candidates if c.strategy == "greedy_value" and c.protection < 1.0]
    assert greedy, "a 0.2 budget should force the unsafe strategy to drop protected text"
    best = pick_best(candidates, require_safe=True)
    assert best is None or best.protection == 1.0


def test_dedupe_only_drops_the_restated_sentence_and_keeps_knowledge():
    spans, atoms = _prepared()
    candidates = build_candidates(
        spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.75,)
    )
    dedupe = next(c for c in candidates if c.strategy == "dedupe_only")
    assert dedupe.token_frac <= 1.0
    assert dedupe.knowledge == 1.0  # every atom's own span survives
    assert dedupe.protection == 1.0


def test_render_pins_the_frontmatter_so_routing_is_not_confounded():
    spans, _atoms = _prepared()
    text = render(spans, [spans[0].id], frontmatter="---\nname: tuning\n---")
    assert text.startswith("---\nname: tuning\n---")
    assert "# Tuning" in text


def test_value_without_measured_inputs_is_flagged_as_a_coverage_proxy():
    """`value` falls back to 0.5 for relevance and outcome, so with neither supplied it is atom
    coverage per token. The candidate must say so rather than presenting it as a value score."""
    spans, atoms = _prepared()
    candidates = build_candidates(
        spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.75,)
    )
    assert candidates
    for c in candidates:
        assert c.value_coverage == 0.0
        assert c.as_dict()["value_is_coverage_proxy"] is True


def test_value_coverage_counts_only_atoms_with_both_a_relevance_and_an_outcome():
    spans, atoms = _prepared()
    relevance = {a.id: {"relevance": 0.9} for a in atoms}
    outcomes = {atoms[0].id: "added"}  # one of three atoms has a measured outcome
    candidates = build_candidates(
        spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.75,),
        relevance=relevance, outcomes=outcomes,
    )
    for c in candidates:
        assert c.value_coverage == round(1 / 3, 4)
        assert c.as_dict()["value_is_coverage_proxy"] is True

    full = {a.id: "added" for a in atoms}
    covered = build_candidates(
        spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.75,),
        relevance=relevance, outcomes=full,
    )
    for c in covered:
        assert c.value_coverage == 1.0
        assert c.as_dict()["value_is_coverage_proxy"] is False


def test_pick_best_falls_back_to_knowledge_when_value_is_a_proxy():
    """Two equally sized candidates: the one with more knowledge wins when neither has measured
    value inputs, instead of being ordered by a number that is not about relevance."""
    from dokimi.novelty.shorten import Candidate

    low = Candidate(strategy="greedy_value_safe", budget_frac=0.5, kept_span_ids=["a"], text="a",
                    tokens=50, tokens_full=100, knowledge=0.4, value=0.9, protection=1.0)
    high = Candidate(strategy="greedy_value_safe", budget_frac=0.5, kept_span_ids=["b"], text="b",
                     tokens=50, tokens_full=100, knowledge=0.8, value=0.1, protection=1.0)
    assert pick_best([low, high]) is high

    low.value_coverage = high.value_coverage = 1.0
    assert pick_best([low, high]) is low
