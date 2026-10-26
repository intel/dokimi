# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""All-vs-all routing: every skill's queries routed against the whole field.

Each skill with queries is the gold in turn, and its queries go to the whole field. The result
is a confusion matrix, each skill's own rate, and the attractors: skills picked for queries
written for other skills. :mod:`dokimi.novelty.routing` holds the single-artifact measurement
and the field it routes against.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

from .client import LLMClient
from .routing import BM25, Candidate, RoutingInputError, model_routing, tokenize

NO_PICK = "(none)"
TIE = "(tie)"


def _no_repeated_key(path: str):
    """A ``json`` object hook that refuses a key written twice, which ``dict`` would merge."""
    def build(pairs: list[tuple[str, object]]) -> dict:
        seen: set[str] = set()
        for key, _value in pairs:
            if key in seen:
                raise RoutingInputError(f"{path}: skill {key!r} appears more than once")
            seen.add(key)
        return dict(pairs)
    return build


def load_queries_by_skill(path: str) -> dict[str, list[str]]:
    """Queries per skill, from a JSON object ``{"<skill name>": ["request", ...]}``.

    Each request is one the named skill should be selected for, written independently of
    it. Blank requests are dropped, and a skill left with none is dropped with them.

    Raises:
        RoutingInputError: The file is not JSON, is not an object, names a skill twice, or a
            value is not a list of strings.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          object_pairs_hook=_no_repeated_key(path))
    except json.JSONDecodeError as exc:
        raise RoutingInputError(f"{path}: not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise RoutingInputError(f"{path}: expected an object of skill name to query list")
    by_skill: dict[str, list[str]] = {}
    for name, listed in data.items():
        if not isinstance(listed, list) or not all(isinstance(q, str) for q in listed):
            raise RoutingInputError(f"{path}: the queries for {name!r} must be a list of strings")
        queries = [q for q in listed if q.strip()]
        if queries:
            by_skill[name] = queries
    return by_skill


def all_vs_all(candidates: list[Candidate], queries_by_skill: dict[str, list[str]], *,
               client: LLMClient | None = None, seed: int = 0) -> dict:
    """Each skill's queries, routed against the whole field: a confusion matrix and attractors.

    Every skill that has queries is the gold in turn. Its queries go to the whole field, and
    the pick is recorded. The lexical arm always runs (free); the model arm runs when
    ``client`` is given. A query no card shares a word with is ``(none)``, not the first card:
    the tie-break in ``lexical_routing`` would otherwise name a card that matched nothing.

    An attractor is a skill picked for queries written for other skills. ``wrongly_picked``
    counts those queries, ``sibling_queries`` is how many queries the other skills have, and
    ``from`` names the skills whose queries it took.

    Returns:
        ``n_skills``, ``n_queries``, and per arm (``lexical``, ``model``): ``confusion`` (gold
        skill to picked skill to count, ``(none)`` and ``(tie)`` included), ``own_rate`` per gold
        skill (``None`` when none of its queries was scored), ``n_scored`` and ``n_unscored``
        per gold skill, and ``attractors`` ordered by ``wrongly_picked``. The model arm also records ``model``,
        ``seed``, ``n_failed``, ``n_abstained`` and ``first_error``.

    Raises:
        RoutingInputError: Fewer than two skills in the field; a name repeated in the field; a
            query file that names a skill outside the field; no skill with any queries.
    """
    names = [c.name for c in candidates]
    _check_all_vs_all_input(names, queries_by_skill)
    result: dict = {
        "n_skills": len(candidates),
        "n_queries": sum(len(q) for q in queries_by_skill.values()),
        "lexical": _attractor_table(*_lexical_picks(candidates, queries_by_skill, names),
                                    names),
    }
    if client is not None:
        result["model"] = _model_arm(candidates, queries_by_skill, names, client, seed)
    return result


def _check_all_vs_all_input(names: list[str], queries_by_skill: dict[str, list[str]]) -> None:
    """Refuse a field or query file :func:`all_vs_all` cannot measure."""
    if len(names) < 2:
        raise RoutingInputError("all-vs-all needs at least two skills in the field")
    if len(set(names)) != len(names):
        raise RoutingInputError("all-vs-all: the field names a skill more than once")
    unknown = sorted(set(queries_by_skill) - set(names))
    if unknown:
        raise RoutingInputError("the queries name skills that are not in the field: "
                                + ", ".join(f"`{n}`" for n in unknown)
                                + "; a skill that sets disable-model-invocation is left out "
                                "unless you pass --include-disabled")
    if not queries_by_skill:
        raise RoutingInputError("no skill in the field has queries to route")


def _lexical_pick(bm25: BM25, tokens: list[str], gold: int, candidates: list[Candidate]) -> str:
    """The card the lexical arm picks for one query written for ``candidates[gold]``.

    The gold is a pick only when it scores strictly above every other card. Two or more other
    cards tied at the top are ``(tie)``, not whichever comes first in the field, so the result
    does not depend on the order of the field. One other card tied with the gold is that card:
    the gold sorts after what it ties with, as in ``lexical_routing``.
    """
    scores = [bm25.score(tokens, i) for i in range(len(candidates))]
    top = max(scores)
    if top <= 0:
        return NO_PICK
    at_top = [i for i, score in enumerate(scores) if math.isclose(score, top, rel_tol=1e-12)]
    others = [i for i in at_top if i != gold]
    if not others:
        return candidates[gold].name
    return candidates[others[0]].name if len(others) == 1 else TIE


def _lexical_picks(candidates: list[Candidate], queries_by_skill: dict[str, list[str]],
                   names: list[str]) -> tuple[dict[str, Counter], dict[str, int]]:
    """The lexical arm of :func:`all_vs_all`: picks per gold skill, and the unscored counts.

    A query with no content word cannot be scored; it is counted in the second dict and left
    out of the picks, as ``lexical_routing`` leaves it out of its rates.
    """
    bm25 = BM25([tokenize(c.card) for c in candidates])
    lexical: dict[str, Counter] = {}
    unscored: dict[str, int] = {}
    for name, queries in queries_by_skill.items():
        picks: Counter = Counter()
        for query in queries:
            tokens = tokenize(query)
            if not tokens:
                unscored[name] = unscored.get(name, 0) + 1
                continue
            picks[_lexical_pick(bm25, tokens, names.index(name), candidates)] += 1
        lexical[name] = picks
    return lexical, unscored


def _model_arm(candidates: list[Candidate], queries_by_skill: dict[str, list[str]],
               names: list[str], client: LLMClient, seed: int) -> dict:
    """The model arm of :func:`all_vs_all`: attractor table plus failure counts."""
    model: dict[str, Counter] = {}
    unscored: dict[str, int] = {}
    n_failed = n_abstained = 0
    first_error = None
    for index, name in enumerate(names):
        if name not in queries_by_skill:
            continue
        routed = model_routing(candidates, queries_by_skill[name], client=client,
                               gold=index, seed=seed + index)
        rows = routed["per_query"]
        n_failed += routed["n_failed"]
        n_abstained += routed["n_abstained"]
        if first_error is None:
            first_error = next((r["error"] for r in rows if r.get("error")), None)
        # A failed call measured nothing: it is not a pick, so it is left out of every rate.
        model[name] = Counter(row.get("picked") or NO_PICK for row in rows if not row.get("error"))
        if routed["n_failed"]:
            unscored[name] = routed["n_failed"]
    return dict(_attractor_table(model, unscored, names), model=client.model, seed=seed,
                n_failed=n_failed, n_abstained=n_abstained, first_error=first_error)


def _attractor_table(picks_by_gold: dict[str, Counter], unscored: dict[str, int],
                     names: list[str]) -> dict:
    """The confusion matrix, own rate and attractor table for one arm of :func:`all_vs_all`.

    Every rate is over the queries that were scored: ``n_scored`` per gold skill, with
    ``n_unscored`` beside it. A skill with none scored has an ``own_rate`` of ``None``, which
    is not the 0.0 of a skill the arm really never picked.
    """
    scored = {gold: sum(picks.values()) for gold, picks in picks_by_gold.items()}
    attractors: list[dict] = []
    for name in names:
        wrong = {gold: picks[name] for gold, picks in picks_by_gold.items()
                 if gold != name and picks[name]}
        if not wrong:
            continue
        sibling_queries = sum(n for gold, n in scored.items() if gold != name)
        attractors.append({
            "skill": name,
            "wrongly_picked": sum(wrong.values()),
            "sibling_queries": sibling_queries,
            "rate": round(sum(wrong.values()) / sibling_queries, 4) if sibling_queries else None,
            "from": sorted(wrong, key=lambda gold: (-wrong[gold], gold)),
        })
    attractors.sort(key=lambda row: (-row["wrongly_picked"], row["skill"]))
    return {
        "confusion": {gold: dict(sorted(picks.items()))
                      for gold, picks in sorted(picks_by_gold.items())},
        "own_rate": {gold: round(picks[gold] / scored[gold], 4) if scored[gold] else None
                     for gold, picks in sorted(picks_by_gold.items())},
        "n_scored": dict(sorted(scored.items())),
        "n_unscored": dict(sorted(unscored.items())),
        "attractors": attractors,
        "note": (f"{NO_PICK} counts a query no card shares a word with; in the model arm it "
                 f"also counts an abstention. {TIE} counts a query on which two or more other "
                 "cards tie for the top score, so no field order decides it. A query with no "
                 "content word, and a failed model call, is not scored: it is in neither the "
                 "confusion matrix nor any rate (see n_unscored)."),
    }
