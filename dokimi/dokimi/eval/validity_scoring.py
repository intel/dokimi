# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Scoring one judge-validity pair: the row type and the (concurrent) scoring loop.

Split out of :mod:`dokimi.eval.judge_validity` to keep that module
under the repo's 500-line ceiling; everything here is re-exported from it.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from dokimi.eval.degrade import NEUTRAL_VARIANTS

#: Aggregate points within which a neutral variant counts as "did not move".
#: The aggregate is a weighted mean rounded to 0.1 (weights sum to 5 without
#: grounding), so 2.0 is exactly what one weight-1 dimension moving 10 points
#: produces: a single coarse rubric step on one dimension still counts as no
#: movement, a step on `correctness` (weight 2) or on two dimensions does not.
NEUTRAL_TOLERANCE = 2.0
#: Float slack on that boundary: abs(62.4 - 64.4) is 2.000000000000007.
_TOLERANCE_SLACK = 1e-9

#: Attempts at one intact answer before its variants are recorded as errored. The
#: pre-cache loop re-scored a failed intact member once per variant (1 to 9
#: attempts); the cache would otherwise error every variant of the base at once.
INTACT_ATTEMPTS = 3

#: Scores an answer. Returns the aggregate 0-100 quality score.
Scorer = Callable[[str, str, str], float]

#: Scores a whole pair at once — a judge that sees both members, e.g.
#: :class:`dokimi.eval.pairwise.PairwiseJudge`. Returns
#: ``(intact_score, degraded_score)`` on the same 0-100 footing.
PairScorer = Callable[[Dict[str, Any]], Tuple[float, float]]


@dataclass
class ValidityRow:
    """One judged pair."""

    base_id: str
    arm: str
    variant: str
    intact_score: Optional[float] = None
    degraded_score: Optional[float] = None
    error: Optional[str] = None
    #: Character counts of the two answers, so the length-only baseline can be
    #: evaluated on exactly the same pairs without re-reading the answer set.
    intact_chars: int = 0
    degraded_chars: int = 0

    @property
    def neutral(self) -> bool:
        """True when the correct outcome is *no movement*, not a win."""
        return self.variant in NEUTRAL_VARIANTS

    @property
    def correct(self) -> Optional[float]:
        """1.0 if ordered right, 0.0 if wrong, 0.5 on a tie, None on error."""
        if self.intact_score is None or self.degraded_score is None:
            return None
        if self.intact_score == self.degraded_score:
            return 0.5
        return 1.0 if self.intact_score > self.degraded_score else 0.0

    @property
    def length_correct(self) -> float:
        """The same call made by ``len(answer)`` alone — the null judge.

        Made on an errored pair too, which the gate counts as misordered, so
        the baseline covers the same pairs as the gate accuracy beside it.
        """
        if self.intact_chars == self.degraded_chars:
            return 0.5
        return 1.0 if self.intact_chars > self.degraded_chars else 0.0

    @property
    def unmoved(self) -> Optional[float]:
        """1.0 when the judge scored both members the same, within tolerance.

        This is the right outcome for a neutral variant, where the degraded copy
        has the same content and only more of it.
        """
        if self.intact_score is None or self.degraded_score is None:
            return None
        return 1.0 if abs(self.intact_score - self.degraded_score) <= NEUTRAL_TOLERANCE + _TOLERANCE_SLACK else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_id": self.base_id,
            "arm": self.arm,
            "variant": self.variant,
            "neutral": self.neutral,
            "intact_score": self.intact_score,
            "degraded_score": self.degraded_score,
            "correct": self.correct,
            "length_correct": self.length_correct,
            "intact_chars": self.intact_chars,
            "degraded_chars": self.degraded_chars,
            "error": self.error,
        }


def _call(scorer: Scorer, pair: Dict[str, Any], answer: str) -> float:
    """Score one member of a pair, with the arm's stored material when the pair carries it."""
    if "context" in pair:
        return float(scorer(pair["library"], pair["question"], answer, context=pair["context"]))  # type: ignore[call-arg]
    return float(scorer(pair["library"], pair["question"], answer))


def _score_pairs(
    scorer: Scorer,
    pairs: Sequence[Dict[str, Any]],
    intact_cache: Dict[tuple, Any],
    *,
    concurrency: int = 1,
    progress: Optional[Callable[[int, int], None]] = None,
    done_offset: int = 0,
    total: int = 0,
    pair_scorer: Optional[PairScorer] = None,
) -> List[ValidityRow]:
    """Score ``pairs``; each intact answer once, then every damaged copy.

    ``intact_cache`` maps (base_id, arm) to a score or to the exception that
    scoring it raised after ``INTACT_ATTEMPTS`` tries, and is shared across calls
    so sequential rounds reuse it. The retries happen here, before any variant is
    scored, so which pairs error never depends on which thread finished first.
    """

    def intact(pair: Dict[str, Any]) -> Tuple[tuple, Any]:
        key = (pair["base_id"], pair.get("arm", ""))
        failure: Exception = RuntimeError("intact answer was not scored")
        for _ in range(INTACT_ATTEMPTS):
            try:
                return key, _call(scorer, pair, pair["intact"])
            except Exception as exc:  # one bad pair must not lose the whole run
                failure = exc
        return key, failure

    def degraded(pair: Dict[str, Any]) -> ValidityRow:
        row = ValidityRow(
            base_id=pair["base_id"],
            arm=pair.get("arm", ""),
            variant=pair["variant"],
            intact_chars=len(pair["intact"]),
            degraded_chars=len(pair["degraded"]),
        )
        if pair_scorer is not None:
            try:
                row.intact_score, row.degraded_score = pair_scorer(pair)
            except Exception as exc:  # one bad pair must not lose the whole run
                row.error = f"{type(exc).__name__}: {exc}"[:200]
            return row
        base = intact_cache[(pair["base_id"], pair.get("arm", ""))]
        try:
            if isinstance(base, Exception):
                raise base
            row.intact_score = base
            row.degraded_score = _call(scorer, pair, pair["degraded"])
        except Exception as exc:  # one bad pair must not lose the whole run
            row.error = f"{type(exc).__name__}: {exc}"[:200]
        return row

    todo: Dict[tuple, Dict[str, Any]] = {}
    for pair in pairs if pair_scorer is None else ():
        key = (pair["base_id"], pair.get("arm", ""))
        if key not in intact_cache:
            todo.setdefault(key, pair)
    total = total or len(pairs)
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        intact_cache.update(pool.map(intact, todo.values()))
        rows: List[ValidityRow] = []
        for index, row in enumerate(pool.map(degraded, pairs), 1):
            rows.append(row)
            if progress:
                progress(done_offset + index, total)
    return rows
