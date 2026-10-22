# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Can this artifact be selected at all, against the alternatives?

Content novelty answers "what does this add once loaded". It deliberately holds the
frontmatter fixed, so it cannot see the failure that precedes everything else: the artifact
is never picked. The existing `skill-agent:` arm offers *one* skill, which makes selection
a binary did-it-load question and cannot see the real failure mode — picking the wrong
sibling out of a family.

Two measurements, reported side by side and never merged into one score:

* **Lexical** — BM25 over each candidate's `name + description`, which is the text a tool
  schema shows. Free, deterministic, and a floor: measured over the 33-skill study corpus
  it ranked the right skill first for only 0.225 of queries drawn from the skills' own
  section headings, and every `dpnp-*` sibling scored 0.0. A description that cannot beat
  BM25 among its siblings is not discriminating; one that can has not yet been shown to be
  picked by a model.
* **Model** — ask a model to choose from the same cards. This is the number people mean by
  "routing", and it is an **upper bound on harness behaviour**: a harness that truncates or
  omits descriptions in its own skill listing selects on less than this measurement shows,
  which has been observed. Only a run inside the harness can settle that.

The query set must come from outside the artifact. Deriving queries from the artifact's own
headings lets the artifact define both the problem scope and its own relevance; that mode
exists for triage, stamps ``queries_self_derived`` into the result, and must not be quoted
as a routing rate.
"""

from __future__ import annotations

import json
import math
import random
import re
import hashlib
from collections import Counter
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from dokimi.skills import Skill
from dokimi.utils import parse_frontmatter

from .client import LLMClient, parse_json_object

# Words, with sentence punctuation left out. A version like `2.1` stays one token, and a
# hyphenated name yields its parts as well as the whole: `dpnp-array-creation` could not
# otherwise be matched by a query that says "array".
_WORD = re.compile(r"[a-z0-9]+(?:[._+-][a-z0-9]+)*")
_SPLITTABLE = re.compile(r"[-_.]")
_STOP = frozenset("""a an the and or of to in on for with without is are be do does how
what when which that this it its use using when-to you your we i if then than from as at
by not no only also can could should would may might must will shall into over under
about""".split())

_SELECT_PROMPT = """\
You are given a user request and a numbered list of available tools. Pick the single tool \
whose description best fits the request, or answer 0 if none fits.

Return ONLY a JSON object: {{"pick": <number>}}

REQUEST:
{query}

TOOLS:
{cards}
"""


@dataclass
class Candidate:
    """One selectable artifact, as the selector sees it."""

    name: str
    description: str

    @property
    def card(self) -> str:
        """The text a tool schema exposes: name plus one-line description, nothing else."""
        return f"{self.name} {self.description}".strip()


def tokenize(text: str) -> list[str]:
    """Content words of ``text``, lowercased, with hyphenated names also split.

    Trailing punctuation used to stay attached, so a description ending in "deadlock."
    could never match a query asking about a deadlock -- which made part of the measured
    BM25 floor a tokenizer artifact rather than a property of the descriptions.
    """
    tokens: list[str] = []
    for word in _WORD.findall(text.lower()):
        if word in _STOP or len(word) < 2:
            continue
        tokens.append(word)
        parts = [p for p in _SPLITTABLE.split(word) if len(p) > 1 and p not in _STOP]
        if len(parts) > 1:
            tokens.extend(parts)
    return tokens


class BM25:
    """Okapi BM25 over a small, fixed candidate set. No index, no dependency."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        if not docs:
            raise ValueError("BM25 needs at least one document")
        self.k1, self.b = k1, b
        self.tf = [Counter(doc) for doc in docs]
        self.lengths = [len(doc) for doc in docs]
        self.avg_length = sum(self.lengths) / len(docs) or 1.0
        document_frequency: Counter = Counter()
        for doc in docs:
            document_frequency.update(set(doc))
        self.idf = {
            term: math.log(1 + (len(docs) - n + 0.5) / (n + 0.5))
            for term, n in document_frequency.items()
        }

    def score(self, query: list[str], index: int) -> float:
        """BM25 score of document ``index`` for ``query``."""
        total = 0.0
        for term in query:
            frequency = self.tf[index].get(term, 0)
            if not frequency:
                continue
            denominator = frequency + self.k1 * (
                1 - self.b + self.b * self.lengths[index] / self.avg_length
            )
            total += self.idf.get(term, 0.0) * frequency * (self.k1 + 1) / denominator
        return total

    def rank(self, query: list[str], *, last: int | None = None) -> list[int]:
        """Document indices, best first. Ties break by index, so the order is stable.

        ``last`` sorts that document after every document it ties with, so a tie (most
        often a query sharing no word with any card) is never scored as its win.
        """
        scored = [(-self.score(query, i), i == last, i) for i in range(len(self.tf))]
        return [index for _score, _is_last, index in sorted(scored)]


def lexical_routing(
    candidates: list[Candidate], queries: list[str], *, gold: int = 0
) -> dict:
    """Rank the gold candidate against its competitors with BM25.

    Args:
        candidates: Every selectable artifact, the one under test included.
        queries: User-shaped requests that *should* select ``gold``.
        gold: Index of the artifact under test.

    Returns:
        ``recall_at_1``, ``recall_at_3``, ``mrr``, ``n_queries``, ``n_candidates``, and the
        per-query rank of the gold card. ``None`` rates when no query had content words.
    """
    if not 0 <= gold < len(candidates):
        raise ValueError(f"gold index {gold} is outside 0..{len(candidates) - 1}")
    bm25 = BM25([tokenize(c.card) for c in candidates])
    ranks: list[int] = []
    per_query: list[dict] = []
    for query in queries:
        tokens = tokenize(query)
        if not tokens:
            per_query.append({"query": query, "rank": None, "reason": "no content words"})
            continue
        order = bm25.rank(tokens, last=gold)
        rank = order.index(gold) + 1
        ranks.append(rank)
        per_query.append({"query": query, "rank": rank,
                          "picked": candidates[order[0]].name})
    n = len(ranks)
    return {
        "method": "bm25_name_description",
        "n_candidates": len(candidates),
        "n_queries_given": len(queries),
        "n_queries_scored": n,
        "n_queries": n,
        "recall_at_1": round(sum(1 for r in ranks if r == 1) / n, 4) if n else None,
        # With three or fewer candidates every rank is <= 3, so the figure would be a
        # structural 1.0 rather than a measurement.
        "recall_at_3": (
            round(sum(1 for r in ranks if r <= 3) / n, 4)
            if n and len(candidates) > 3 else None
        ),
        "mrr": round(sum(1 / r for r in ranks) / n, 4) if n else None,
        "per_query": per_query,
        "note": (
            "A floor, and free. Over the 33-skill study corpus BM25 put the right skill "
            "first for 0.225 of queries drawn from the skills' own headings, and 0.0 for "
            "every `dpnp-*` sibling: descriptions discriminate a domain, not a sibling."
        ),
    }


def _is_pick_reply(reply: str, n_cards: int) -> bool:
    """A reply worth caching: a ``pick`` that is ``null`` or a card number in ``0..n_cards``.

    ``0`` and ``null`` are explicit abstentions. A reply cut at ``{"pick":``, or a pick that
    names no card (``"abc"``, ``2.9``, ``[1]``, ``999``) still counts as an abstention for
    this run, but is not stored, so a later run with ``DOKIMI_RESPONSE_CACHE`` set asks again.
    """
    try:
        obj = parse_json_object(reply)
    except ValueError:
        return False
    if "pick" not in obj:
        return False
    pick = obj["pick"]
    if pick is None:
        return True
    if isinstance(pick, bool) or not isinstance(pick, int | float | str):
        return False
    if isinstance(pick, float) and not pick.is_integer():
        return False
    if isinstance(pick, str) and not pick.strip().isdigit():
        return False
    return 0 <= _as_pick_position(pick) <= n_cards


def _as_pick_position(value: object) -> int:
    """Card number from a model's ``pick``, or 0 when it is not one (null, prose, 2.9, true)."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else 0
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return 0


def model_routing(
    candidates: list[Candidate], queries: list[str], *, client: LLMClient, gold: int = 0,
    seed: int = 0,
) -> dict:
    """Ask a model to pick from the same cards, one call per query.

    The candidate order is a seeded random permutation per query, so a model that always
    picks the first (or last) option scores about ``1/N`` rather than a figure set by how
    the queries happen to line up with the candidate count. A rotation would not do that:
    with 5 queries over 33 candidates it shows the gold card at position 1, then last, then
    second-to-last -- exactly the positions where primacy and recency bias are strongest.
    The permutation is recorded per query, so a result can be re-read or replayed.

    Returns:
        ``selection_rate``, ``n_queries``, ``n_abstained``, ``n_failed``, the per-query pick
        with the position the gold card was shown at, and the caveat that this bounds
        harness behaviour from above.
    """
    if not 0 <= gold < len(candidates):
        raise ValueError(f"gold index {gold} is outside 0..{len(candidates) - 1}")
    picks: list[dict] = []
    for index_of_query, query in enumerate(queries):
        shuffler = random.Random(f"{seed}:{index_of_query}:{len(candidates)}")
        order = list(range(len(candidates)))
        shuffler.shuffle(order)
        cards = "\n".join(
            f"{position + 1}. {candidates[index].card}"
            for position, index in enumerate(order)
        )
        try:
            reply = client.text(
                _SELECT_PROMPT.format(query=query, cards=cards),
                accept=partial(_is_pick_reply, n_cards=len(order)),
            )
        except Exception as exc:  # noqa: BLE001 - one failed query must not lose the rest
            picks.append({"query": query, "picked": None, "error": str(exc)[:200]})
            continue
        # A reply that is not a usable pick is the model declining to route, not a
        # transport failure: it stays in the denominator like an explicit abstention.
        try:
            raw = _as_pick_position(parse_json_object(reply).get("pick", 0))
        except ValueError:
            raw = 0
        if raw < 1 or raw > len(order):
            picks.append({"query": query, "picked": None, "abstained": True})
            continue
        chosen = order[raw - 1]
        picks.append({"query": query, "picked": candidates[chosen].name,
                      "correct": chosen == gold, "picked_at": raw,
                      "gold_shown_at": order.index(gold) + 1,
                      "order": [candidates[i].name for i in order]})

    answered = [p for p in picks if "correct" in p]
    hits = sum(1 for p in answered if p["correct"])
    attempted = len(picks) - sum(1 for p in picks if p.get("error"))
    return {
        "method": "model_choice_over_cards",
        "model": client.model,
        "n_candidates": len(candidates),
        "n_queries": len(queries),
        "n_abstained": sum(1 for p in picks if p.get("abstained")),
        "n_failed": sum(1 for p in picks if p.get("error")),
        "seed": seed,
        "card_order": "seeded random permutation per query",
        # Abstaining *is* the routing failure, so it belongs in the denominator. The
        # among-answered rate is kept beside it, never alone: reporting only that let a
        # model that picked gold once and abstained nine times report 1.0.
        "selection_rate": round(hits / attempted, 4) if attempted else None,
        "selection_rate_among_answered": (
            round(hits / len(answered), 4) if answered else None
        ),
        "per_query": picks,
        "note": (
            "An upper bound on harness behaviour, not a harness measurement: a harness "
            "whose own skill listing truncates or omits descriptions selects on less text "
            "than this. Only a run inside the harness settles that."
        ),
    }


def headings_as_queries(body: str, *, limit: int = 40) -> list[str]:
    """Section headings of an artifact, for triage only.

    Queries taken from the artifact let it define both the problem scope and its own
    relevance. Callers must stamp ``queries_self_derived`` on any result built from these.
    """
    headings = [h.strip() for h in re.findall(r"^#{2,3}\s+(.+)$", body, re.M)]
    return [h for h in headings if len(tokenize(h)) >= 2][:limit]


def non_skill_reason(ref: str) -> str | None:
    """Why a ``skill:`` reference names something other than a skill, or ``None``.

    A shell glob over a skills directory hands the field every file beside the skills (a
    ``.mjs`` helper, a ``registry.json``), and a directory with no ``SKILL.md`` in it is not a
    skill either. Only ``skill:`` references are checked: a ``text:`` or plain path is a text
    artifact, which is a legitimate competitor.
    """
    if not ref.startswith("skill:"):
        return None
    path = Path(ref.removeprefix("skill:"))
    if path.is_file() and path.name != "SKILL.md":
        return f"{path.name} is not a SKILL.md"
    if path.is_dir() and not (path / "SKILL.md").is_file():
        return "the directory has no SKILL.md"
    return None


def skill_file(ref: str) -> Path | None:
    """The ``SKILL.md`` a ``skill:`` reference names, or ``None`` for any other reference."""
    if not ref.startswith("skill:"):
        return None
    path = Path(ref.removeprefix("skill:"))
    return path / "SKILL.md" if path.is_dir() else path


def disables_model_invocation(skill_md: Path) -> bool:
    """Whether a ``SKILL.md`` sets ``disable-model-invocation``, so no harness lists it.

    The model cannot select such a skill, so a card for it measures a choice that does not
    exist. The truthy spellings are the ones ``repos.budget`` reads, so the two agree.
    """
    meta, _body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    return str(meta.get("disable-model-invocation", "")).strip().lower() in (
        "1", "true", "yes", "on")


class RoutingInputError(ValueError):
    """A competitor field or query file in which selection is not a measurement."""


def load_candidate(ref: str) -> tuple[Candidate, str, str | None]:
    """An artifact as the selector sees it: ``(card, body, warning)``.

    Raises:
        FileNotFoundError, ValueError: The artifact cannot be loaded.
    """
    from .analyze import load_artifact_text

    kind, name, description, _frontmatter, body = load_artifact_text(ref)
    warning = (
        f"{ref} has no description; its card is a name, which is all a tool schema "
        "would show" if kind == "skill" and not description else None
    )
    return Candidate(name=name or ref, description=description), body, warning


def catalog_competitors(root: str, exclude: set[str],
                        notes: list[str] | None = None, *,
                        include_disabled: bool = False) -> list[Candidate]:
    """Every skill under ``root`` whose name is not in ``exclude``.

    The field a harness would list next to the artifact, rather than one the caller picks.
    Skills come from the catalog reader, so a ``skills.yaml`` manifest decides which skills
    the catalog has. A declared skill that cannot be loaded is left out and described in
    ``notes`` (when given) rather than aborting the run. A skill with
    ``disable-model-invocation`` is left out too, unless ``include_disabled``. A mirror, a
    byte-identical ``SKILL.md`` under another path (a plugin copy of the ``skills/`` tree), is
    left out too: the copy nearest the catalog root stands for the card.

    Raises:
        ValueError: ``root`` is not a directory, or a skill under it cannot be loaded (with
            no manifest every ``SKILL.md`` is read, and one that is broken still raises).
        RoutingInputError: Two skills in the field share a name with different content; the
            message names both paths.
    """
    from dokimi.external.catalog import read_skill_manifest

    if not Path(root).is_dir():
        raise ValueError(f"--catalog is not a directory: {root}")
    eligible: list[tuple[Skill, str]] = []
    for entry in read_skill_manifest(Path(root)):
        skill = entry.skill
        if skill is None or not entry.loaded:
            if notes is not None:
                notes.append(f"skipped catalog entry {entry.name!r}: {entry.error}")
            continue
        if skill.name in exclude:
            continue
        if not include_disabled and disables_model_invocation(skill.path):
            if notes is not None:
                notes.append(f"left out catalog skill {skill.name!r}: "
                             "disable-model-invocation (--include-disabled keeps it)")
            continue
        eligible.append((skill, hashlib.sha256(skill.path.read_bytes()).hexdigest()))

    # Two skills with one name and different bytes cannot both stand for the card, and
    # check_field would only say "pass each competitor once", which is not the problem.
    copies_by_name: dict[str, list[tuple[str, str]]] = {}
    for skill, digest in eligible:
        copies_by_name.setdefault(skill.name, []).append((skill.path.as_posix(), digest))
    for name in sorted(copies_by_name):
        copies = sorted(copies_by_name[name])
        first_path, first_digest = copies[0]
        other = next((path for path, digest in copies if digest != first_digest), None)
        if other is not None:
            raise RoutingInputError(
                f"catalog has {name} at {first_path} and {other} with "
                "different content; dedupe or rename"
            )

    # The copy with the fewest path parts stands for each byte-identical group; ties go to
    # the path that sorts first, so the choice does not depend on directory order.
    keeper: dict[str, Skill] = {}
    for skill, digest in eligible:
        best = keeper.get(digest)
        if best is None or (len(skill.path.parts), skill.path.as_posix()) < (
                len(best.path.parts), best.path.as_posix()):
            keeper[digest] = skill
    candidates: list[Candidate] = []
    for skill, digest in eligible:
        if keeper[digest] is not skill:
            if notes is not None:
                notes.append(f"left out catalog skill {skill.name!r} at {skill.path}: "
                             f"identical to {keeper[digest].path}")
            continue
        candidates.append(Candidate(name=skill.name, description=skill.description))
    return candidates


def check_field(gold: Candidate, competitors: list[Candidate]) -> None:
    """Refuse a competitor field in which selection is not a measurement.

    Raises:
        RoutingInputError: No competitors (that measures whether the model uses a tool at
            all); the artifact among its own competitors (the tie break puts the gold card
            last, so it scores 0 against itself); a competitor named twice (counted twice
            in "vs N competitors", and splitting the lexical tie-break between two copies
            of one card, so the rates describe a different field).
    """
    if not competitors:
        raise RoutingInputError(
            "routing needs competitors (--competitors, or --catalog for a skill's siblings): "
            "selecting from one candidate measures whether the model will use a tool at all, "
            "not whether this artifact wins"
        )
    if any(c.name == gold.name for c in competitors):
        raise RoutingInputError(
            f"`{gold.name}` appears in --competitors as well as --artifact; selection "
            "against itself is not a measurement. Drop it from the competitor list."
        )
    names = [c.name for c in competitors]
    name_counts = Counter(names)
    repeated = sorted({n for n, count in name_counts.items() if count > 1})
    if repeated:
        raise RoutingInputError(f"--competitors names {', '.join(f'`{n}`' for n in repeated)} "
                                "more than once; pass each competitor once")


def load_queries(path: str) -> list[str]:
    """Queries from a JSON list, ``{"queries": [...]}``, or one query per line.

    Blank queries are dropped, so the result can be empty.

    Raises:
        RoutingInputError: The file holds something other than a list of strings. A file
            that cannot be read raises as reading it does.
    """
    raw = Path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, (list, dict)):      # one-per-line, e.g. "[urgent] build fails"
        data = raw.splitlines()
    listed = data if isinstance(data, list) else data.get("queries", [])
    # A string here is iterable, so `{"queries": "a b"}` once became one query per
    # character and a run over 0 scored queries exited 0.
    if not isinstance(listed, list) or not all(isinstance(q, str) for q in listed):
        raise RoutingInputError(f"{path}: queries must be a list of strings, "
                                "or one query per line")
    return [q for q in listed if q.strip()]


NO_PICK = "(none)"


def load_queries_by_skill(path: str) -> dict[str, list[str]]:
    """Queries per skill, from a JSON object ``{"<skill name>": ["request", ...]}``.

    Each request is one the named skill should be selected for, written independently of
    it. Blank requests are dropped, and a skill left with none is dropped with them.

    Raises:
        RoutingInputError: The file is not JSON, is not an object, or a value is not a list
            of strings.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
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
        skill to picked skill to count, ``(none)`` included), ``own_rate`` per gold skill, and
        ``attractors`` ordered by ``wrongly_picked``. The model arm also records ``seed``.

    Raises:
        RoutingInputError: Fewer than two skills in the field; a name repeated in the field; a
            query file that names a skill outside the field; no skill with any queries.
    """
    names = [c.name for c in candidates]
    if len(candidates) < 2:
        raise RoutingInputError("all-vs-all needs at least two skills in the field")
    if len(set(names)) != len(names):
        raise RoutingInputError("all-vs-all: the field names a skill more than once")
    unknown = sorted(set(queries_by_skill) - set(names))
    if unknown:
        raise RoutingInputError("the queries name skills that are not in the field: "
                                + ", ".join(f"`{n}`" for n in unknown))
    if not queries_by_skill:
        raise RoutingInputError("no skill in the field has queries to route")

    bm25 = BM25([tokenize(c.card) for c in candidates])
    lexical: dict[str, Counter] = {}
    for name, queries in queries_by_skill.items():
        picks: Counter = Counter()
        for query in queries:
            tokens = tokenize(query)
            order = bm25.rank(tokens) if tokens else []
            best = order[0] if order else None
            if best is None or bm25.score(tokens, best) <= 0:
                picks[NO_PICK] += 1
            else:
                picks[candidates[best].name] += 1
        lexical[name] = picks
    result: dict = {
        "n_skills": len(candidates),
        "n_queries": sum(len(q) for q in queries_by_skill.values()),
        "lexical": _attractor_table(lexical, queries_by_skill, names),
    }
    if client is not None:
        model: dict[str, Counter] = {}
        for index, name in enumerate(names):
            if name not in queries_by_skill:
                continue
            rows = model_routing(candidates, queries_by_skill[name], client=client, gold=index,
                                 seed=seed + index)["per_query"]
            model[name] = Counter(row.get("picked") or NO_PICK for row in rows)
        result["model"] = dict(_attractor_table(model, queries_by_skill, names), seed=seed)
    return result


def _attractor_table(picks_by_gold: dict[str, Counter], queries_by_skill: dict[str, list[str]],
                     names: list[str]) -> dict:
    """The confusion matrix, own rate and attractor table for one arm of :func:`all_vs_all`."""
    attractors: list[dict] = []
    for name in names:
        wrong = {gold: picks[name] for gold, picks in picks_by_gold.items()
                 if gold != name and picks[name]}
        if not wrong:
            continue
        sibling_queries = sum(len(queries_by_skill[gold]) for gold in picks_by_gold
                              if gold != name)
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
        "own_rate": {gold: round(picks[gold] / len(queries_by_skill[gold]), 4)
                     for gold, picks in sorted(picks_by_gold.items())},
        "attractors": attractors,
        "note": (f"{NO_PICK} counts a query no card shares a word with; in the model arm it "
                 "also counts an abstention and a failed call."),
    }
