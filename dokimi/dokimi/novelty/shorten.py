# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Propose shorter artifacts, and score each candidate on five axes that never collapse.

One "retention" number hides the failure that matters. Measured: a candidate at **21% of
the original tokens** kept **0.916** of the knowledge and **0.158** of the protected
spans. Knowledge called it a success. What it had thrown away were negations, version
gates and ordering constraints — exactly the spans a value ranking discards, because
their value is conditional on a failure their statement does not encode.

So every candidate reports:

``tokens``      how much smaller
``knowledge``   atoms still recoverable (retention oracle)
``value``       retention weighted by relevance × novelty, with the *coverage* of those
                weights reported beside it: both inputs fall back to 0.5, so at low
                coverage this axis is atom coverage per token wearing a value label
``protection``  share of protected spans still present
``routing``     whether the description still claims the artifact's own queries

Routing is ``1.0`` by construction here, because frontmatter is pinned and never
selected against — and that was checked, not assumed: shortened candidates in the study
were measured routing-invariant. Two other measured facts shape the defaults: no
shortened candidate lost more than 0.08 against the full artifact on a strong model, every
task-score loss beyond −0.08 belonged to the smallest model, and some artifacts cannot be
shortened at all (one pinned at 0.94 token fraction under any safety-preserving strategy, because
nearly every span was protected or a sole carrier).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .atoms import Atom
from .retention import RetentionOracle
from .spans import Span

STRATEGIES = ("greedy_value", "greedy_value_safe", "dedupe_only", "random")


@dataclass
class Candidate:
    """One proposed shortening and its five scores."""

    strategy: str
    budget_frac: float
    kept_span_ids: list[str]
    text: str
    tokens: int
    tokens_full: int
    knowledge: float
    value: float
    protection: float
    routing: float = 1.0
    # Share of this artifact's atoms for which `value` had a *measured* relevance and outcome.
    # Both inputs default to 0.5 when absent, so at low coverage `value` silently degenerates into
    # atom-coverage-per-token. Measured in the study: per-model value was defined for 3-15 atoms per
    # artifact (148 of 2422), because it needs a per-model K0 and that exists only for probed atoms,
    # so the axis read as "value" was mostly coverage. The number ships next to the rate.
    value_coverage: float = 0.0
    dropped_protected_spans: list[str] = field(default_factory=list)

    @property
    def token_frac(self) -> float:
        """Kept tokens as a fraction of the original."""
        return round(self.tokens / self.tokens_full, 4) if self.tokens_full else 1.0

    @property
    def safe(self) -> bool:
        """Whether this candidate may be accepted at all.

        A candidate that drops a protected span is not a smaller artifact; it is a
        different one, with a failure mode the knowledge score cannot see.
        """
        return self.protection >= 1.0

    def as_dict(self) -> dict:
        """Serializable form."""
        return {
            "strategy": self.strategy,
            "budget_frac": self.budget_frac,
            "token_frac": self.token_frac,
            "tokens": self.tokens,
            "tokens_full": self.tokens_full,
            "knowledge": self.knowledge,
            "value": self.value,
            "value_coverage": self.value_coverage,
            "value_is_coverage_proxy": self.value_coverage < 0.5,
            "protection": self.protection,
            "routing": self.routing,
            "safe": self.safe,
            "kept_spans": len(self.kept_span_ids),
            "dropped_protected_spans": list(self.dropped_protected_spans),
        }


def span_value(
    span: Span,
    atoms_by_span: dict[str, list[Atom]],
    *,
    relevance: dict[str, dict] | None = None,
    outcomes: dict[str, str] | None = None,
    categories: dict[str, str] | None = None,
) -> float:
    """Value density of a span: relevance × novelty per token.

    A conflicting atom contributes *negatively*: keeping the span is worse than dropping
    it, and a selector that treated harm as merely worthless would keep it whenever it
    was cheap.
    """
    relevance = relevance or {}
    outcomes = outcomes or {}
    categories = categories or {}
    total = 0.0
    for atom in atoms_by_span.get(span.id, []):
        weight = relevance.get(atom.id, {}).get("relevance", 0.5)
        if categories.get(atom.id) == "harmful_conflicting":
            total -= 1.0
            continue
        novelty = {"added": 1.0, "still_missing": 0.6, "redundant": 0.15, "degraded": -0.5}.get(
            outcomes.get(atom.id, ""), 0.5
        )
        total += weight * novelty
    return total / max(1, span.tokens)


def build_candidates(
    spans: list[Span],
    atoms: list[Atom],
    *,
    oracle: RetentionOracle,
    budgets: tuple[float, ...] = (0.75, 0.5),
    strategies: tuple[str, ...] = STRATEGIES,
    relevance: dict[str, dict] | None = None,
    outcomes: dict[str, str] | None = None,
    categories: dict[str, str] | None = None,
    pin_kinds: tuple[str, ...] = ("heading",),
    seed: int = 0,
) -> list[Candidate]:
    """Build and score one candidate per (strategy, budget).

    Args:
        budgets: Target token fractions. A budget is a target, not a promise: the safe
            strategies refuse to go below what protection and sole carriers require, and
            report the token fraction they actually reached.
        pin_kinds: Span kinds always kept (headings, so the document stays navigable and
            routing stays comparable).

    Spans no extraction call could read (``Span.unread`` with no atom) are kept by every
    strategy. They carry no atom because nobody read them, so a cut could not be scored:
    the knowledge axis would read 1.0 over a candidate that dropped whatever they say.
    """
    atoms_by_span: dict[str, list[Atom]] = {span.id: [] for span in spans}
    for atom in atoms:
        for span_id in atom.span_ids:
            if span_id in atoms_by_span:
                atoms_by_span[span_id].append(atom)
    carrier_count: dict[str, int] = {}
    for atom in atoms:
        carrier_count[atom.id] = len([s for s in atom.span_ids if s in atoms_by_span])
    sole_span_ids = {
        span.id for span in spans
        if any(carrier_count.get(a.id) == 1 for a in atoms_by_span.get(span.id, []))
    }

    tokens_full = sum(span.tokens for span in spans)
    protected_ids = {span.id for span in spans if span.protected}
    unread_ids = {span.id for span in spans if span.unread and not atoms_by_span[span.id]}
    candidates: list[Candidate] = []

    for strategy in strategies:
        for budget in budgets:
            kept = _select(
                strategy, spans, atoms_by_span,
                budget=budget, tokens_full=tokens_full, pin_kinds=pin_kinds,
                protected_ids=protected_ids, sole_span_ids=sole_span_ids,
                unread_ids=unread_ids,
                relevance=relevance, outcomes=outcomes, categories=categories, seed=seed,
            )
            candidates.append(_score(
                strategy, budget, kept, spans, atoms, oracle,
                tokens_full=tokens_full, protected_ids=protected_ids,
                relevance=relevance, outcomes=outcomes,
            ))
            if strategy == "dedupe_only":
                break  # deduplication ignores the budget; one candidate is the answer
    return candidates


def _select(
    strategy: str,
    spans: list[Span],
    atoms_by_span: dict[str, list[Atom]],
    *,
    budget: float,
    tokens_full: int,
    pin_kinds: tuple[str, ...],
    protected_ids: set[str],
    sole_span_ids: set[str],
    relevance: dict[str, dict] | None,
    outcomes: dict[str, str] | None,
    categories: dict[str, str] | None,
    seed: int,
    unread_ids: set[str] | frozenset[str] = frozenset(),
) -> list[str]:
    target = budget * tokens_full
    by_id = {span.id: span for span in spans}
    pinned = [s.id for s in spans if s.kind in pin_kinds or s.id in unread_ids]
    if strategy == "greedy_value_safe":
        pinned += [s for s in protected_ids] + [s for s in sole_span_ids]
    pinned_set = set(pinned)
    spent = sum(by_id[s].tokens for s in pinned_set)

    if strategy == "dedupe_only":
        # Keep the first span carrying each atom, plus every span carrying none — the
        # question this strategy answers is "is the bulk duplicated?", and answering it
        # honestly means not also deleting the un-attributed text.
        seen: set[str] = set()
        kept = []
        for span in spans:
            own = {a.id for a in atoms_by_span.get(span.id, [])}
            if not own or own - seen:
                kept.append(span.id)
                seen |= own
        return kept

    candidates = [s for s in spans if s.id not in pinned_set]
    if strategy == "random":
        rng = random.Random(f"novelty-shorten-{seed}")
        rng.shuffle(candidates)
    else:
        candidates.sort(
            key=lambda s: span_value(
                s, atoms_by_span, relevance=relevance, outcomes=outcomes, categories=categories
            ),
            reverse=True,
        )

    kept_set = set(pinned_set)
    for span in candidates:
        if spent + span.tokens > target:
            continue
        kept_set.add(span.id)
        spent += span.tokens
    return [s.id for s in spans if s.id in kept_set]


def render(spans: list[Span], kept_span_ids: list[str], *, frontmatter: str = "") -> str:
    """Render a candidate as text, in document order, frontmatter pinned.

    Frontmatter is prepended verbatim and never selected against: it is what the router
    sees, so varying it would confound a content measurement with a routing one.
    """
    kept = set(kept_span_ids)
    body = "\n\n".join(span.text for span in spans if span.id in kept)
    return f"{frontmatter.rstrip()}\n\n{body}" if frontmatter else body


def _score(
    strategy: str,
    budget: float,
    kept_span_ids: list[str],
    spans: list[Span],
    atoms: list[Atom],
    oracle: RetentionOracle,
    *,
    tokens_full: int,
    protected_ids: set[str],
    relevance: dict[str, dict] | None,
    outcomes: dict[str, str] | None,
) -> Candidate:
    kept = set(kept_span_ids)
    text = render(spans, kept_span_ids)
    tokens = sum(span.tokens for span in spans if span.id in kept)

    retained = {atom.id: oracle.verdict(atom, text).retained for atom in atoms}
    knowledge = round(sum(retained.values()) / len(atoms), 4) if atoms else 1.0

    weights = {
        atom.id: (relevance or {}).get(atom.id, {}).get("relevance", 0.5)
        * {"added": 1.0, "still_missing": 0.7, "redundant": 0.2, "degraded": 0.0}.get(
            (outcomes or {}).get(atom.id, ""), 0.5)
        for atom in atoms
    }
    weight_total = sum(weights.values())
    value = round(
        sum(w for atom_id, w in weights.items() if retained.get(atom_id)) / weight_total, 4
    ) if weight_total else 1.0

    measured = sum(
        1 for atom in atoms
        if (relevance or {}).get(atom.id, {}).get("relevance") is not None
        and (outcomes or {}).get(atom.id)
    )
    value_coverage = round(measured / len(atoms), 4) if atoms else 0.0

    kept_protected = protected_ids & kept
    protection = round(len(kept_protected) / len(protected_ids), 4) if protected_ids else 1.0

    return Candidate(
        strategy=strategy,
        budget_frac=budget,
        kept_span_ids=kept_span_ids,
        text=text,
        tokens=tokens,
        tokens_full=tokens_full,
        knowledge=knowledge,
        value=value,
        value_coverage=value_coverage,
        protection=protection,
        dropped_protected_spans=sorted(protected_ids - kept),
    )


def pick_best(candidates: list[Candidate], *, require_safe: bool = True) -> Candidate | None:
    """Smallest candidate that keeps its protected spans, preferring higher value.

    With ``require_safe=False`` the caller gets the smallest candidate regardless — which
    is how the 0.158-protection candidate got built, and why the default is on.
    """
    pool = [c for c in candidates if c.safe] if require_safe else list(candidates)
    pool = [c for c in pool if c.strategy != "dedupe_only" and c.token_frac < 0.999]
    if not pool:
        return None

    # Tie-break on `value` only where value had measured inputs. Below half coverage it is atom
    # coverage per token, and knowledge is then the honest secondary key — same quantity, no claim
    # about relevance the run cannot support.
    def rank(c: Candidate) -> tuple[float, float]:
        return (c.token_frac, -(c.value if c.value_coverage >= 0.5 else c.knowledge))

    return sorted(pool, key=rank)[0]
