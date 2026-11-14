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
  section headings. Siblings from one template (`dpnp-*`) share heading queries, so for a
  family that figure measures the template. A description that cannot beat BM25 among its
  siblings on independent queries is not discriminating; one that can is not yet shown picked.
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
import os
import random
import re
from collections import Counter
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from dokimi.external.listing import only_description_too_long
from dokimi.skills import Skill
from dokimi.utils import InputError, parse_frontmatter

from .catalog_keep import check_keep_copies_under, keep_past_broken_copies, resolve_kept_copies
from .client import LLMClient, OutputTruncated, SpendCapReached, parse_json_object
from .noise import partial_record, repeat_noise
from .routing_stop import PERMANENT_STOP, is_run_wide

if TYPE_CHECKING:
    from dokimi.external.validate import SkillReview

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
            "first for 0.225 of queries drawn from the skills' own headings. Template siblings "
            "share heading queries: check sibling confusion with independently written ones."
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


# A reply that declines to route is kept verbatim, up to this many characters.
_RAW_REPLY_CHARS = 2000


def _pick_row(base: dict, reply: str, order: list[int], candidates: list[Candidate],
              gold: int) -> dict:
    """One model reply as a per-query row: a pick, or an abstention with the raw reply kept.

    A reply that is not a usable pick is the model declining to route, not a transport failure:
    it stays in the denominator like an explicit abstention, and its raw text is kept so the
    decline can be read rather than inferred.
    """
    try:
        raw = _as_pick_position(parse_json_object(reply).get("pick", 0))
    except ValueError:
        raw = 0
    if raw < 1 or raw > len(order):
        return {**base, "picked": None, "abstained": True,
                "raw_reply": reply[:_RAW_REPLY_CHARS]}
    chosen = order[raw - 1]
    return {**base, "picked": candidates[chosen].name,
            "correct": chosen == gold, "picked_at": raw,
            "gold_shown_at": order.index(gold) + 1,
            "order": [candidates[i].name for i in order]}



def _ask_once(client, prompt: str, base: dict, order: list[int], candidates: list[Candidate],
              gold: int) -> tuple[dict | None, str | None]:
    """One call of the routing loop: its row, and the message when the cap or a permanent error stops the run.

    A call the cap refused has no row; one with no price was paid for, so its reply is scored (a cut one as a decline) and the caller stops.
    A run-wide provider error (:func:`dokimi.novelty.routing_stop.is_run_wide`) has no row either and stops the run.
    """
    try:
        reply = client.text(prompt, accept=partial(_is_pick_reply, n_cards=len(order)),
                            draw=base["repeat"])
    except (SpendCapReached, OutputTruncated) as exc:
        stop = str(exc) if isinstance(exc, SpendCapReached) else None
        if exc.reply is None:
            return None, stop
        if isinstance(exc, OutputTruncated) or exc.truncated:  # cut at the ceiling: a decline, as unparseable is
            return {**base, "picked": None, "abstained": True, "raw_reply": exc.reply[:_RAW_REPLY_CHARS]}, stop
        return _pick_row(base, exc.reply, order, candidates, gold), stop
    except Exception as exc:  # noqa: BLE001 - one failed query must not lose the rest
        if is_run_wide(exc):
            # No credential, wrong model ID or missing permission fails every later call alike (#677).
            return None, PERMANENT_STOP +(str(exc)[:200] or type(exc).__name__)
        return {**base, "picked": None, "error": str(exc)[:200] or type(exc).__name__}, None
    return _pick_row(base, reply, order, candidates, gold), None


def _ask_query(client, query: str, index_of_query: int, candidates: list[Candidate], gold: int,
               seed: int, repeats: int) -> tuple[list[dict], str | None]:
    """The repeats of one query: its rows, and the cap's message when the cap stops the run.

    The card order is seeded per query, and kept across its repeats (see :func:`model_routing`).
    """
    shuffler = random.Random(f"{seed}:{index_of_query}:{len(candidates)}")
    order = list(range(len(candidates)))
    shuffler.shuffle(order)
    cards = "\n".join(
        f"{position + 1}. {candidates[index].card}"
        for position, index in enumerate(order)
    )
    prompt = _SELECT_PROMPT.format(query=query, cards=cards)
    group: list[dict] = []
    for repeat in range(repeats):
        row, stopped = _ask_once(client, prompt, {"query": query, "repeat": repeat},
                                 order, candidates, gold)
        if row is not None:
            group.append(row)
        if stopped is not None:
            return group, stopped
    return group, None


def model_routing(
    candidates: list[Candidate], queries: list[str], *, client: LLMClient, gold: int = 0,
    seed: int = 0, repeats: int = 1,
) -> dict:
    """Ask a model to pick from the same cards, ``repeats`` times per query.

    The candidate order is a seeded random permutation per query, so a model that always
    picks the first (or last) option scores about ``1/N`` rather than a figure set by how
    the queries happen to line up with the candidate count. A rotation would not do that:
    with 5 queries over 33 candidates it shows the gold card at position 1, then last, then
    second-to-last -- exactly the positions where primacy and recency bias are strongest.
    The permutation is recorded per query, so a result can be re-read or replayed.

    Each query keeps one card order across its repeats, so the spread between repeats is
    the model's own sampling noise rather than a change of layout. Each repeat is its own
    sample (``draw``), so with the response cache on a repeat is not a replay.

    Returns:
        ``selection_rate``, ``n_queries``, ``n_calls`` (queries x repeats; ``n_abstained`` and
        ``n_failed`` count calls), ``n_abstained``, ``n_failed``, the per-query pick
        with the position the gold card was shown at, the raw reply of every abstention or
        unparseable pick, and the caveat that this bounds harness behaviour from above.
        ``noise`` is always an object (see :func:`repeat_noise`). With ``repeats > 1`` it holds
        each query's agreement across its repeats and the run-to-run spread of the selection rate;
        with one repeat ``n_repeats`` is 1 and the agreement and spread fields are ``None``.

        When the client's ``--max-usd`` cap refuses a call, or a call comes back unpriced, the
        loop stops and the result holds the calls made so far, with ``cap_stop`` (the cap's
        message). A run-wide provider error (:func:`~dokimi.novelty.routing_stop.is_run_wide`)
        stops it the same way, with the error in ``cap_stop`` and ``stop_reason``
        ``"permanent_error"`` (else ``"spend_cap"``); any other error is a row of its query. A
        ``partial`` block (:func:`partial_record`) is added only when planned calls
        were left unmade (``n_calls`` below queries x repeats). A query stopped between repeats
        keeps its rows but is left out of ``noise``. Without a cap that trips, there is no
        ``cap_stop`` key.
    """
    if not 0 <= gold < len(candidates):
        raise ValueError(f"gold index {gold} is outside 0..{len(candidates) - 1}")
    if repeats < 1:
        raise ValueError(f"repeats must be at least 1, not {repeats}")
    picks: list[dict] = []
    groups: list[list[dict]] = []
    capped: str | None = None
    for index_of_query, query in enumerate(queries):
        group, stopped = _ask_query(client, query, index_of_query, candidates, gold, seed, repeats)
        picks += group  # the calls already paid for stay in ``picks`` when the cap stops
        if len(group) == repeats:  # a query stopped half-way is no sample of the repeat noise
            groups.append(group)
        if stopped is not None:
            capped = stopped
            break

    answered = [p for p in picks if "correct" in p]
    hits = sum(1 for p in answered if p["correct"])
    attempted = len(picks) - sum(1 for p in picks if p.get("error"))
    result = {
        "method": "model_choice_over_cards",
        "model": client.model,
        "n_candidates": len(candidates),
        "n_queries": len(queries),
        "repeats": repeats,
        "n_calls": len(picks),
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
        "noise": repeat_noise(groups, repeats),
        "per_query": picks,
        "note": (
            "An upper bound on harness behaviour, not a harness measurement: a harness "
            "whose own skill listing truncates or omits descriptions selects on less text "
            "than this. Only a run inside the harness settles that."
        ),
    }
    if capped is not None:
        # The stop is kept apart from ``partial``: a stop on the run's last planned call leaves
        # nothing unmade, but a caller (all-vs-all) still must not ask a later skill unmetered.
        result["cap_stop"] = capped
        reason = "permanent_error" if capped.startswith(PERMANENT_STOP) else "spend_cap"
        result["stop_reason"] = reason
        if len(picks) < len(queries) * repeats:
            result["partial"] = partial_record(capped, len(picks), len(queries) * repeats, reason)
    return result


# Only spaces indent a fence (a tab is four columns, so it makes indented code), and a
# backtick opener may not carry a backtick in its info string; both are CommonMark rules.
_FENCE = re.compile(r" {0,3}(`{3,}(?!.*`)|~{3,})")


def _outside_fences(body: str) -> str:
    """``body`` with its fenced code blocks removed, so a ``## comment`` in a shell block is
    not read as a heading. A fence closes on the same character, at least as long, alone on
    its line; an unclosed fence runs to the end of the body, as CommonMark has it.
    """
    kept: list[str] = []
    opening: str | None = None
    for line in body.splitlines():
        fence = _FENCE.match(line)
        if opening is None:
            if fence:
                opening = fence.group(1)
            else:
                kept.append(line)
        elif (fence and fence.group(1)[0] == opening[0] and len(fence.group(1)) >= len(opening)
              and not line.strip()[len(fence.group(1)):]):
            opening = None
    return "\n".join(kept)


def headings_as_queries(body: str, *, limit: int = 40) -> list[str]:
    """Section headings of an artifact, for triage only.

    Queries taken from the artifact let it define both the problem scope and its own
    relevance. Callers must stamp ``queries_self_derived`` on any result built from these.
    Headings inside fenced code blocks are not headings, so they are not offered.
    """
    headings = [h.strip() for h in re.findall(r"^#{2,3}[ \t]+(.+)$", _outside_fences(body), re.M)]
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


def over_limit_warnings(competitors: list[Candidate], *, under_test: bool = False) -> list[dict]:
    """One record per skill whose description is over the 1024-character limit.

    ``competitors`` are skills of the field, or, with ``under_test``, the skill under test,
    which the message names as such.
    """
    from dokimi.external.validate import DESCRIPTION_MAX

    kept = ("the skill under test, measured with its full description" if under_test
            else "kept in the field with its full description")
    return [{"skill": c.name, "length": len(c.description), "limit": DESCRIPTION_MAX,
             "message": f"description is {len(c.description)} characters, over the {DESCRIPTION_MAX} "
                        f"limit; {kept} (harnesses differ: "
                        "pi.dev rejects the skill, Claude Code truncates past about 1,536 "
                        "characters)"}
            for c in competitors if len(c.description) > DESCRIPTION_MAX]


def competitor_left_out(ref: str, *, include_disabled: bool, include_invalid: bool) -> str | None:
    """Why an explicit ``skill:`` competitor is left out of the field, as the note says, or None.

    The same two rules as a catalog skill: a ``disable-model-invocation`` skill (unless
    ``include_disabled``) and a skill with a spec error (unless ``include_invalid``). A
    description over 1024 characters, as the only spec error, does not leave a skill out.
    """
    from dokimi.external.catalog import SkillCatalogEntry
    from dokimi.external.validate import review_skills
    from dokimi.skills import load_skill

    path = skill_file(ref)
    if path is None:
        return None
    # absolute first: a relative ``skill:SKILL.md`` has parent.parent == "." and would be read as a
    # root skill (no directory-name check), so the same skill would get a different verdict by spelling
    path = Path(os.path.abspath(path))
    if not include_disabled and disables_model_invocation(path):
        return "disable-model-invocation (--include-disabled keeps it)"
    if include_invalid:
        return None
    skill = load_skill(path)
    # A SKILL.md with no ``skills/`` parent is a root skill, as the catalog reads one at its scan root:
    # its directory name says nothing about the skill, so the name check is skipped for it.
    root = path.parent.parent if path.parent.parent.name == "skills" else path.parent
    entry = SkillCatalogEntry(name=skill.name, status=None, path=path.parent,
                              source_root=root, skill=skill)
    review = review_skills([entry])[0]
    if review is None or review.state != "error" or only_description_too_long(review):
        return None
    problems = "; ".join(f.message for f in review.findings if f.level == "error")
    return f"spec error ({problems}; --include-invalid keeps it)"


def left_out_rules(ref: str, reason: str, *, include_invalid: bool) -> set[str]:
    """Each rule (``disabled``, ``invalid``) that leaves a ``skill:`` competitor out of the field.

    ``reason`` is what :func:`competitor_left_out` returned for ``ref``. It names the first rule
    only, since a disabled skill is not checked for a spec error, so that check is made here.
    """
    if reason.startswith("spec error"):
        return {"invalid"}
    also = competitor_left_out(ref, include_disabled=True, include_invalid=include_invalid)
    return {"disabled", "invalid"} if also else {"disabled"}


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


class RoutingInputError(InputError):
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
                        include_disabled: bool = False,
                        include_invalid: bool = False,
                        dropped: dict[str, set[str]] | None = None,
                        keep_copies_under: str | None = None,
                        conflicts: dict[str, list[str]] | None = None,
                        unloaded: dict[str, str] | None = None) -> list[Candidate]:
    """Every skill under ``root`` whose name is not in ``exclude``.

    The field a harness would list next to the artifact, rather than one the caller picks.
    Skills come from the catalog reader, so a ``skills.yaml`` manifest decides which skills
    the catalog has. A declared skill that cannot be loaded is left out and described in
    ``notes`` (when given) rather than aborting the run. A skill with
    ``disable-model-invocation`` is left out too, unless ``include_disabled``. A skill with a
    spec error (``catalog check`` reports it as an error, e.g. a name that is not its directory
    name) is left out too, unless ``include_invalid``. A description over 1024 characters, as the
    only error, keeps the skill (harnesses differ on it; see ``only_description_too_long``). A mirror, a byte-identical
    ``SKILL.md`` under another path (a plugin copy of the ``skills/`` tree), is left out too:
    the copy nearest the catalog root stands for the card. Disabled and spec-error copies are
    filtered according to the flags first. A name whose remaining copies differ in content leaves
    the field whole, with a note naming every remaining path of that name. Each name a flag or a
    conflict leaves out is mapped in ``dropped`` (when given) to why: ``disabled``, ``invalid``
    or ``conflict``, so a caller need not read the catalog again.

    ``keep_copies_under`` (a directory under ``root``) keeps only the copies under it for a name
    whose copies differ and that have one content under it; the others of that name are left out
    with a note. ``conflicts`` (when given) gets each name still in conflict, mapped to the paths of
    its copies. ``unloaded`` (when given) gets each non-excluded skill that did not load, with why.

    Raises:
        ValueError: ``root`` is not a directory, ``keep_copies_under`` is not a directory inside
            it (see :func:`check_keep_copies_under`), or its ``skills.yaml`` is invalid (with no
            manifest every ``SKILL.md`` is read, and a broken one is skipped with a note).
    """
    if not Path(root).is_dir():
        raise ValueError(f"the catalog is not a directory: {root}")
    if keep_copies_under is not None:
        check_keep_copies_under(root, keep_copies_under)
    eligible = _eligible_catalog_skills(root, exclude, notes, include_disabled, include_invalid,
                                        dropped, keep_copies_under, conflicts, unloaded)
    keeper = _keepers_by_copy(eligible)
    candidates: list[Candidate] = []
    for skill, digest in eligible:
        if keeper[(skill.name, digest)] is not skill:
            if notes is not None:
                notes.append(f"left out catalog skill {skill.name!r} at {skill.path}: "
                             f"identical to {keeper[(skill.name, digest)].path}")
            continue
        candidates.append(Candidate(name=skill.name, description=skill.description))
    return candidates


def _eligible_catalog_skills(root: str, exclude: set[str], notes: list[str] | None,
                             include_disabled: bool,
                             include_invalid: bool = False,
                             dropped: dict[str, set[str]] | None = None,
                             keep_copies_under: str | None = None,
                             conflicts: dict[str, list[str]] | None = None,
                             unloaded: dict[str, str] | None = None,
                             ) -> list[tuple[Skill, str]]:
    """Each loadable, non-excluded catalog skill with the sha256 of its ``SKILL.md``.

    Disabled and spec-error copies are filtered according to the flags first. A name whose
    remaining copies differ in content leaves the field whole, with a note naming every
    remaining path of that name.
    """
    from dokimi.external.catalog import read_skill_manifest
    from dokimi.external.validate import review_skills

    entries = read_skill_manifest(Path(root), notes)
    reviews = review_skills(entries)
    loaded: list[tuple[Skill, str, SkillReview]] = []
    broken: dict[str, list[Path | None]] = {}
    for entry, review in zip(entries, reviews, strict=True):
        skill = entry.skill
        if skill is None or not entry.loaded or review is None or review.content_sha256 is None:
            if unloaded is not None and entry.name not in exclude:  # a SKILL.md unreadable after load: no digest
                unloaded[entry.name] = entry.error or "SKILL.md cannot be read"
                broken.setdefault(entry.name, []).append(entry.path)
            if notes is not None:
                notes.append(f"skipped catalog entry {entry.name!r}: {entry.error or 'SKILL.md cannot be read'}")
            continue
        if skill.name in exclude:
            continue
        # the digest and the disabled flag are the review's, so routing and catalog check cannot disagree
        loaded.append((skill, review.content_sha256 or "", review))
    # the flags first: a copy no harness lists (disabled, or a spec error) does not make its name a conflict
    listed: list[tuple[Skill, str]] = []
    for skill, digest, review in loaded:
        if not _left_out_by_flag(skill, review, include_disabled, include_invalid, notes, dropped):
            listed.append((skill, digest))
    listed = resolve_kept_copies(listed, root, keep_copies_under, notes)
    conflicting = _conflicting_names(listed, notes, dropped, conflicts)
    field = [(skill, digest) for skill, digest in listed if skill.name not in conflicting]
    keep_past_broken_copies(field, root, keep_copies_under, broken, unloaded, notes)
    return field


def _left_out_by_flag(skill: Skill, review: SkillReview, include_disabled: bool,
                      include_invalid: bool, notes: list[str] | None,
                      dropped: dict[str, set[str]] | None = None) -> bool:
    """Whether a disabled or spec-error skill is left out, with the note that says why.

    The note names the first rule; ``dropped`` (when given) gets every rule that applies.
    """
    disabled = not include_disabled and review.disable_model_invocation
    invalid = (not include_invalid and review.state == "error"
               and not only_description_too_long(review))
    if dropped is not None and (disabled or invalid):
        dropped.setdefault(skill.name, set()).update(
            why for why, hit in (("disabled", disabled), ("invalid", invalid)) if hit)
    if disabled:
        if notes is not None:
            notes.append(f"left out catalog skill {skill.name!r}: "
                         "disable-model-invocation (--include-disabled keeps it)")
        return True
    if invalid:
        if notes is not None:
            problems = "; ".join(f.message for f in review.findings if f.level == "error")
            notes.append(f"left out catalog skill {skill.name!r} at {skill.path}: "
                         f"spec error ({problems}; --include-invalid keeps it)")
        return True
    return False


def _conflicting_names(listed: list[tuple[Skill, str]], notes: list[str] | None,
                       dropped: dict[str, set[str]] | None = None,
                       conflicts: dict[str, list[str]] | None = None) -> set[str]:
    """The names that two different files among ``listed`` share, with a note naming each path.

    Two skills with one name and different bytes cannot both stand for the card, so the name
    leaves the field. The rest of the field stays. ``listed`` holds only the copies the flags
    let into the field, so ``catalog check`` names the same copies as conflicting.
    """
    digests: dict[str, set[str]] = {}
    for skill, digest in listed:
        digests.setdefault(skill.name, set()).add(digest)
    conflicting = {name for name, found in digests.items() if len(found) > 1}
    if dropped is not None:
        for name in conflicting:
            dropped.setdefault(name, set()).add("conflict")
    if conflicts is not None:
        for name in conflicting:
            conflicts[name] = sorted(skill.path.as_posix() for skill, _ in listed
                                     if skill.name == name)
    if notes is not None:
        for name in sorted(conflicting):
            paths = ", ".join(sorted(skill.path.as_posix() for skill, _ in listed
                                     if skill.name == name))
            notes.append(f"skipped catalog name {name!r}: it has different content at "
                         f"{paths}; keep one directory's copies with --keep-copies-under <dir>, "
                         "or dedupe or rename them to route them")
    return conflicting


def _keepers_by_copy(eligible: list[tuple[Skill, str]]) -> dict[tuple[str, str], Skill]:
    """The copy with the fewest path parts stands for each group of identical copies.

    A group is one name with one digest: two skills with identical text but different names
    (``skills/a`` and ``skills/b`` without a frontmatter name) are two cards, not copies.
    Ties go to the path that sorts first, so the choice does not depend on directory order.
    """
    keeper: dict[tuple[str, str], Skill] = {}
    for skill, digest in eligible:
        group = (skill.name, digest)
        best = keeper.get(group)
        if best is None or (len(skill.path.parts), skill.path.as_posix()) < (
                len(best.path.parts), best.path.as_posix()):
            keeper[group] = skill
    return keeper


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
            "routing needs competitors (--competitors, or --against for a skill's siblings): "
            "selecting from one candidate measures whether the model will use a tool at all, "
            "not whether this artifact wins"
        )
    if any(c.name == gold.name for c in competitors):
        raise RoutingInputError(
            f"`{gold.name}` appears in --competitors as well as the skill under test; selection "
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
        RoutingInputError: The file cannot be read (missing, unreadable or not UTF-8), or
            holds something other than a list of strings.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise RoutingInputError(f"cannot read --queries {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):  # JSONDecodeError is a ValueError
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
