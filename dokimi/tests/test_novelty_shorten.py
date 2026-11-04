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


class _Asked(RetentionOracle):
    """The lexical oracle, recording which atoms it was asked about."""

    def __init__(self):
        super().__init__(client=None)
        self.asked: set[str] = set()

    def verdict(self, atom, text):
        self.asked.add(atom.id)
        return super().verdict(atom, text)


def test_a_span_whose_only_atom_is_unsampled_is_a_sole_carrier_and_the_oracle_sees_the_sample():
    """A capped run: s0002's atom is outside the sample, the protected spans' are in it (#371)."""
    spans, atoms = _prepared()
    unsampled, sample = atoms[0], atoms[1:]
    oracle = _Asked()
    candidates = build_candidates(spans, atoms, oracle=oracle, budgets=(0.3,), scored_atoms=sample)
    # Its knowledge is not scored, so the oracle's pairs follow the sample, not every atom.
    assert oracle.asked == {atom.id for atom in sample}
    safe = next(c for c in candidates if c.strategy == "greedy_value_safe")
    assert unsampled.span_ids[0] in safe.kept_span_ids  # still carries knowledge: kept, not free
    assert safe.knowledge == 1.0  # both sampled atoms kept, over a denominator of two
    # Without a sample (no cap, or every atom kept) the candidates are those of every atom.
    every = build_candidates(spans, atoms, oracle=RetentionOracle(), budgets=(0.3,),
                             scored_atoms=atoms)
    default = build_candidates(spans, atoms, oracle=RetentionOracle(), budgets=(0.3,))
    assert [c.as_dict() for c in every] == [c.as_dict() for c in default]


def _four_one_atom_spans():
    """Four unprotected spans of 20, 10, 20 and 10 tokens, one atom each."""
    from dokimi.novelty.spans import Span

    spans, atoms = [], []
    for i, (word, chars) in enumerate((("alpha", 80), ("bravo", 40), ("charlie", 80),
                                       ("delta", 40)), start=1):
        text = " ".join([word] * 20)[:chars]
        spans.append(Span(id=f"s{i:04d}", kind="sentence", text=text, start=0, end=chars))
        atoms.append(Atom(statement=text, question=f"Q {word}", answer="a", span_ids=(f"s{i:04d}",)))
    index_atoms(spans, atoms)
    assert [span.tokens for span in spans] == [20, 10, 20, 10]
    return spans, atoms


def test_a_cut_that_drops_every_carrier_of_an_unsampled_atom_is_not_safe_and_not_picked():
    """A cap of two sampled the 2nd and 4th spans' atoms; the oracle never scores the others."""
    spans, atoms = _four_one_atom_spans()
    candidates = build_candidates(spans, atoms, oracle=RetentionOracle(client=None),
                                  budgets=(0.5,), scored_atoms=[atoms[1], atoms[3]])
    greedy = next(c for c in candidates if c.strategy == "greedy_value")
    assert greedy.kept_span_ids == ["s0002", "s0004"] and greedy.knowledge == 1.0
    assert greedy.dropped_unscored_atoms == 2 and greedy.safe is False
    assert greedy.as_dict()["dropped_unscored_atoms"] == 2
    assert all(c.dropped_unscored_atoms == 0 for c in candidates if c.safe)
    assert pick_best(candidates) is not greedy
    # Uncapped, the oracle scores every atom, so the same cut's loss shows in `knowledge`.
    uncapped = build_candidates(spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.5,))
    assert all(c.dropped_unscored_atoms == 0 for c in uncapped)
    greedy = next(c for c in uncapped if c.strategy == "greedy_value")
    assert (greedy.knowledge, greedy.safe) == (0.5, True)


def test_the_safe_strategy_keeps_the_cheapest_span_of_an_unsampled_atom_cited_twice():
    """Six spans; unsampled a2 is cited by s0002 (20 tokens) and s0003 (10), so neither is a
    sole carrier, and the four sole carriers alone are over the 0.5 budget."""
    from dokimi.novelty.spans import Span

    words = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
    spans = [Span(id=f"s{i:04d}", kind="sentence", text=" ".join([w] * 20)[:80 if i == 2 else 40],
                  start=0, end=80 if i == 2 else 40) for i, w in enumerate(words, start=1)]
    cites = (("s0001",), ("s0002", "s0003"), ("s0004",), ("s0005",), ("s0006",))
    atoms = [Atom(statement=f"fact {i}", question=f"Q {i}", answer="a", span_ids=ids)
             for i, ids in enumerate(cites, start=1)]
    index_atoms(spans, atoms)
    assert [span.tokens for span in spans] == [10, 20, 10, 10, 10, 10]
    sample = [atom for atom in atoms if atom is not atoms[1]]
    candidates = build_candidates(spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.5,),
                                  strategies=("greedy_value_safe",), scored_atoms=sample)
    safe = candidates[0]
    assert "s0003" in safe.kept_span_ids and "s0002" not in safe.kept_span_ids
    assert (safe.dropped_unscored_atoms, safe.safe) == (0, True)
    assert pick_best(candidates) is safe
    # No cap: a2 is scored, so nothing beyond protection and sole carriers is pinned.
    uncapped = build_candidates(spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.5,),
                                strategies=("greedy_value_safe",))
    assert not {"s0002", "s0003"} & set(uncapped[0].kept_span_ids)


def test_the_safe_strategy_pins_nothing_for_an_unsampled_atom_already_kept():
    """Unsampled a2 cites s0001, which is pinned as a1's sole carrier: s0002 stays droppable."""
    from dokimi.novelty.spans import Span

    words = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot")
    spans = [Span(id=f"s{i:04d}", kind="sentence", text=" ".join([w] * 20)[:80 if i == 1 else 40],
                  start=0, end=80 if i == 1 else 40) for i, w in enumerate(words, start=1)]
    cites = (("s0001",), ("s0001", "s0002"), ("s0003",), ("s0004",), ("s0005",), ("s0006",))
    atoms = [Atom(statement=f"fact {i}", question=f"Q {i}", answer="a", span_ids=ids)
             for i, ids in enumerate(cites, start=1)]
    index_atoms(spans, atoms)
    assert [span.tokens for span in spans] == [20, 10, 10, 10, 10, 10]
    sample = [atom for atom in atoms if atom is not atoms[1]]
    safe = build_candidates(spans, atoms, oracle=RetentionOracle(client=None), budgets=(0.3,),
                            strategies=("greedy_value_safe",), scored_atoms=sample)[0]
    assert safe.kept_span_ids == ["s0001", "s0003", "s0004", "s0005", "s0006"]
    assert (safe.dropped_unscored_atoms, safe.safe) == (0, True)
