# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The retention oracle: "is this atom still recoverable from that text?"

Needed whenever a candidate shortening has to be scored, and the honest answer is not a
similarity number. On 714 constructed pairs, embedding and lexical matchers scored
**0.00 recall on the contradiction label** — they cannot tell *restates* from
*reverses*, so a purely vector-based oracle would score a reversed claim as faithfully
retained. Macro-F1 measured: direct LLM adjudication 0.953, retrieval→NLI→LLM hybrid
0.848 at 25.6% of the LLM calls, NLI alone 0.732, embeddings ~0.44.

So the oracle is a cascade. Cheap lexical evidence decides the two confident ends;
everything in the ambiguous band escalates to a model, and every adjudication is cached
on disk keyed by (atom, text digest) because the shortener asks about the same atom
against many candidates.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .atoms import Atom
from .client import LLMClient

RETAINED = "retained"
ABSENT = "absent"
CONTRADICTED = "contradicted"
_VERDICTS = (RETAINED, ABSENT, CONTRADICTED)
_CACHE_VERSION = b"retention-cache-v2\0"

_STOP = frozenset("""
a an the and or but if then than that this these those is are was were be been being
to of in on at for with from by as it its not no do does did can could should would
you your we our they their he she them use used using when while into over under
""".split())

_ADJUDICATE_PROMPT = """\
Can the STATEMENT be recovered from the TEXT below — same claim, same polarity, same
qualifiers, same numbers and versions?

STATEMENT: {statement}

TEXT:
{text}

Return ONLY JSON: {{"verdict": "retained" | "absent" | "contradicted", "why": "<clause>"}}

- "retained": the text asserts it (wording may differ).
- "absent": the text neither asserts nor denies it.
- "contradicted": the text asserts something incompatible with it — including the same
  claim with a flipped polarity, a different number, or a different version bound.
"""


@dataclass
class RetentionVerdict:
    """One oracle answer, with the method that produced it recorded."""

    verdict: str
    method: str  # "lexical" | "llm" | "cache" | "error"
    score: float

    @property
    def retained(self) -> bool:
        """Whether the atom survives — a contradiction is *not* retention."""
        return self.verdict == RETAINED


class RetentionOracle:
    """Cascade oracle over one candidate text at a time.

    Args:
        client: Adjudicator for the ambiguous band. Without one the oracle stays
            lexical, which is cheap and reproducible but cannot see contradictions —
            :attr:`escalations` then reports 0 and the caller should say so.
        low: Overlap at or below which the atom is called ``absent`` without a model.
        high: Overlap at or above which it is called ``retained`` without a model.
        cache_dir: Where adjudications are memoized.
    """

    def __init__(
        self,
        *,
        client: LLMClient | None = None,
        low: float = 0.25,
        high: float = 0.85,
        cache_dir: Path | None = None,
    ) -> None:
        self.client = client
        self.low = low
        self.high = high
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.escalations = 0
        self.decisions = 0
        self.errors = 0
        self._memo: dict[str, RetentionVerdict] = {}

    def verdict(self, atom: Atom, text: str) -> RetentionVerdict:
        """Decide whether ``atom`` survives in ``text``."""
        key = _key(atom, text)
        cached = self._memo.get(key) or self._load(key)
        if cached is not None:
            self.decisions += 1
            return cached

        score = lexical_overlap(atom.statement, text)
        if score >= self.high:
            result = RetentionVerdict(RETAINED, "lexical", round(score, 4))
        elif score <= self.low or self.client is None:
            # Without an adjudicator the ambiguous band resolves to `absent`: the
            # conservative direction, which under-reports retention rather than
            # claiming an atom survived that may have been reversed.
            result = RetentionVerdict(ABSENT, "lexical", round(score, 4))
        else:
            self.escalations += 1
            verdict = self._adjudicate(atom, text)
            if verdict is None:
                # No usable verdict (failed call, unparseable reply, or one naming no
                # verdict): answer `absent` for this decision, but neither memoize nor
                # store it, so a transient error does not become a cached verdict that
                # every later run replays.
                self.errors += 1
                self.decisions += 1
                return RetentionVerdict(ABSENT, "error", round(score, 4))
            result = RetentionVerdict(verdict, "llm", round(score, 4))

        self.decisions += 1
        self._memo[key] = result
        self._store(key, result)
        return result

    def retained_fraction(self, atoms: list[Atom], text: str) -> float:
        """Share of ``atoms`` the oracle finds in ``text``."""
        if not atoms:
            return 1.0
        kept = sum(1 for atom in atoms if self.verdict(atom, text).retained)
        return round(kept / len(atoms), 4)

    def stats(self) -> dict:
        """What the oracle did — escalation rate is the cost knob worth reporting."""
        return {
            "decisions": self.decisions,
            "llm_escalations": self.escalations,
            "escalation_rate": (
                round(self.escalations / self.decisions, 4) if self.decisions else 0.0
            ),
            "llm_errors": self.errors,
            "band": [self.low, self.high],
            "adjudicator": self.client.model if self.client else None,
        }

    def _adjudicate(self, atom: Atom, text: str) -> str | None:
        """The adjudicator's verdict, or ``None`` when there is no usable one.

        ``None`` covers a failed call, a reply that does not parse, and a reply that
        parses but names no verdict (``{"verdict": null}``, no key, an unknown word).
        Reading the last kind as ``absent`` made it a real-looking verdict: uncounted in
        ``llm_errors`` and written to the disk cache. The same test gates the response
        cache, so an unusable reply is not replayed from there either.
        """
        assert self.client is not None
        try:
            reply = self.client.text(
                _ADJUDICATE_PROMPT.format(statement=atom.statement, text=text[:12000]),
                accept=lambda raw: parse_verdict(raw) is not None,
            )
        except Exception:
            return None
        return parse_verdict(reply)

    def _path(self, key: str) -> Path | None:
        return (self.cache_dir / f"{key}.json") if self.cache_dir else None

    def _load(self, key: str) -> RetentionVerdict | None:
        path = self._path(key)
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["verdict"] not in _VERDICTS:
                return None
            return RetentionVerdict(data["verdict"], "cache", float(data.get("score", 0.0)))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _store(self, key: str, result: RetentionVerdict) -> None:
        path = self._path(key)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"verdict": result.verdict, "score": result.score}), "utf-8")


def error_warning(oracle: RetentionOracle) -> str | None:
    """The warning for adjudications with no usable verdict, or ``None`` when there were none.

    Shared by ``analyze`` and ``novelty shorten``, which both score candidates with the
    oracle, so a throttled adjudicator is never silent in either.
    """
    if not oracle.errors:
        return None
    return (
        f"{oracle.errors} of {oracle.escalations} retention adjudications got no usable "
        "verdict (failed call, unparseable reply, or no verdict named) and were answered "
        "`absent` (not cached); carrier confirmation and shortening scores under-count "
        "retention by up to that many decisions"
    )


def parse_verdict(reply: str) -> str | None:
    """The verdict an adjudicator reply names, or ``None`` when it names none.

    Case and surrounding punctuation are normalised first (``"Retained"``,
    ``"retained."``), because those replies do carry a verdict. A question mark is not
    stripped: ``"retained?"`` is a hedge, not a verdict, and reading it as ``retained``
    would err in the direction that over-counts retention.
    """
    from .client import parse_json_object

    try:
        raw = parse_json_object(reply).get("verdict")
    except ValueError:
        return None
    if not isinstance(raw, str):
        return None
    verdict = raw.strip().strip(".,;:!'\"`*").strip().lower()
    return verdict if verdict in _VERDICTS else None


def lexical_overlap(statement: str, text: str) -> float:
    """Content-word overlap of ``statement`` with ``text``, in [0, 1].

    A prefilter, not a matcher: it decides only the confident ends of the range. Numbers
    and flags are kept as tokens because they are what a shortening most often drops.
    """
    want = _content_words(statement)
    if not want:
        return 0.0
    have = _content_words(text)
    return len(want & have) / len(want)


def _content_words(text: str) -> set[str]:
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.\-]+|\d+(?:\.\d+)?", (text or "").lower())
    return {t for t in tokens if t not in _STOP and len(t) > 1}


def _key(atom: Atom, text: str) -> str:
    digest = hashlib.sha256()
    # Bumped when what a cache entry means changes. v2: entries written before failed or
    # verdict-less adjudications were kept out of the cache can hold such a failure
    # stored as `absent`, indistinguishable from a real one, so they are not read.
    digest.update(_CACHE_VERSION)
    digest.update(atom.statement.strip().encode("utf-8"))
    digest.update(b"\0")
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()[:32]
