# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Sequential judge-validity gate: add bases until each variant is decided.

A fixed 20-base run leaves most variants inconclusive — a bootstrap interval
of +-0.15 straddles a 0.80 gate for any judge between 0.65 and 0.95 — while a
variant sitting at 0.98 was settled after a handful of pairs. Wald's sequential
probability ratio test spends pairs only where they are needed: each round adds
a batch of bases, and a variant stops taking pairs the moment the evidence for
PASS or FAIL crosses the boundary set by the two error rates.

Simulated by resampling 90 Haiku outcomes per variant (3 re-draws of 30 pairs,
treated as independent, pre-#52 judge prompt), fixed n=20 was inconclusive on
roughly a quarter to four fifths of runs for shuffle / strip_code / wrong_tools
/ wrong_numbers, and SPRT decided ~99% correctly with a mean of 15-27 pairs.
None of those variants sat inside the 0.72-0.88 indifference band, where the
test is weakest. Live, on five seeds of the same answers, the fixed gate said
PASS once and FAIL four times, all inconclusive; this one said FAIL five times.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from dokimi.eval.degrade import DEFAULT_MIN_CHARS, DEGRADATIONS, NEUTRAL_VARIANTS, _answer_pool
from dokimi.eval.judge_validity import (
    DEFAULT_MIN_ACCURACY,
    PairScorer,
    Scorer,
    ValidityReport,
    _build,
    _score_pairs,
)

#: Sequential mode tests H0 "accuracy = gate - delta" against H1 "accuracy =
#: gate + delta". A true accuracy inside that band is a judge the gate cannot
#: usefully separate from the threshold, and the test may run to the cap on it.
SPRT_DELTA = 0.08

#: Both hypotheses stay inside [1 - SPRT_CEILING, SPRT_CEILING], symmetric about
#: the gate: delta shrinks to fit. Clamping only the upper one (to 1 - 1e-6) made
#: a miss cost -12 log-likelihood against +0.14 per hit, so at gate 0.95 a judge
#: at 0.99 failed 26% of runs while the report printed alpha=beta=0.05. Below a
#: delta of SPRT_MIN_DELTA the test cannot decide in a feasible number of pairs
#: (at 0.98 even an all-hit judge needs 150 pairs, past the default cap of 120),
#: so such a gate is refused (ValueError; exit 2 in the command).
SPRT_CEILING = 0.99
SPRT_MIN_DELTA = 0.02

#: Error rates for the sequential test: P(PASS | at H0) and P(FAIL | at H1).
SPRT_ALPHA = 0.05
SPRT_BETA = 0.05

#: Upper bound on bases in sequential mode. On the measured judge every
#: variant outside the indifference band decided by ~50 pairs at the 90th
#: percentile; the cap only bounds the cost of a variant sitting inside it.
SPRT_MAX_BASES = 120

#: Neutral variants (not gated) stop taking pairs after this many bases. Without
#: a cap they rode along with the slowest gated variant: 100 padding pairs on
#: gpt-oss while wrong_numbers sat in the indifference band.
NEUTRAL_MAX_BASES = 30

#: Bases added per sequential round.
SPRT_BATCH = 10


def sprt_bounds(gate: float, delta: float = SPRT_DELTA) -> Tuple[float, float]:
    """``(p0, p1)`` of the test: ``gate -/+ d`` with ``d = delta`` shrunk to fit the ceiling."""
    d = min(delta, SPRT_CEILING - gate, gate - (1 - SPRT_CEILING))
    if d < SPRT_MIN_DELTA - 1e-9:
        low = 1 - SPRT_CEILING + SPRT_MIN_DELTA
        raise ValueError(
            f"--min-accuracy {gate:g} is outside {low:.2f}-{SPRT_CEILING - SPRT_MIN_DELTA:.2f}, "
            "where the sequential test can decide; omit --sequential to use the fixed-size gate"
        )
    return gate - d, gate + d


def sprt(
    outcomes: Sequence[float],
    gate: float,
    *,
    delta: float = SPRT_DELTA,
    alpha: float = SPRT_ALPHA,
    beta: float = SPRT_BETA,
) -> Tuple[str, float]:
    """Wald's SPRT over pair outcomes. Returns ``(decision, log-likelihood ratio)``.

    ``decision`` is ``"PASS"``, ``"FAIL"`` or ``"continue"``. A tie (0.5) enters
    as half a success. That is not neutral: with the default 0.72 vs 0.88 a tie
    moves the ratio toward FAIL (-0.32 each), matching the fixed gate, where a
    tie also scores below the 0.80 threshold.
    """
    p0, p1 = sprt_bounds(gate, delta)
    win, loss = math.log(p1 / p0), math.log((1 - p1) / (1 - p0))
    llr = sum(x * win + (1 - x) * loss for x in outcomes)
    if llr >= math.log((1 - beta) / alpha):
        return "PASS", llr
    if llr <= math.log(beta / (1 - alpha)):
        return "FAIL", llr
    return "continue", llr


def run_sequential(
    answers: Sequence[Dict[str, Any]],
    scorer: Scorer,
    *,
    judge: str = "unknown",
    max_bases: int = SPRT_MAX_BASES,
    batch: int = SPRT_BATCH,
    seed: int = 0,
    variants: Optional[Sequence[str]] = None,
    library: str = "the library",
    min_accuracy: float = DEFAULT_MIN_ACCURACY,
    progress: Optional[Callable[[int, int], None]] = None,
    concurrency: int = 1,
    pair_scorer: Optional[PairScorer] = None,
    store: Optional[Any] = None,
) -> ValidityReport:
    """Like :func:`run_validity`, but add bases until every variant is decided.

    Pairs are built for ``max_bases`` up front (degradation is free), then
    scored ``batch`` bases at a time. After each round every undecided graded
    variant gets an SPRT verdict; decided ones take no more pairs. Neutral
    variants are scored only while something is still undecided, since nothing
    is gated on them.
    """
    sprt_bounds(min_accuracy)  # refuse an undecidable gate before any pair is built or scored
    noop: Dict[str, int] = {}
    dropped: List[int] = [0]
    by_arm: Dict[str, Dict[str, int]] = {}
    pairs = _build(answers, noop, n_bases=max_bases, seed=seed, variants=variants, library=library,
                   store=store, dropped=dropped, by_arm=by_arm)
    order: List[tuple] = list(dict.fromkeys((p["base_id"], p.get("arm", "")) for p in pairs))
    graded = sorted({p["variant"] for p in pairs if p["variant"] not in NEUTRAL_VARIANTS})
    decisions: Dict[str, Dict[str, Any]] = {v: {"decision": "continue", "llr": 0.0, "n": 0} for v in graded}
    report = ValidityReport(judge=judge, min_accuracy=min_accuracy, seed=seed, sequential=decisions)
    report.n_noop = noop
    report.variants = list(variants or DEGRADATIONS)
    report.context = "stored" if store is not None else "none"
    report.n_without_context = dropped[0]
    report.by_arm = by_arm
    intact_cache: Dict[tuple, Any] = {}
    started = time.time()
    for start in range(0, len(order), batch):
        open_variants = {v for v, d in decisions.items() if d["decision"] == "continue"}
        if not open_variants:
            break
        chunk = set(order[start:start + batch])
        todo = [
            p for p in pairs
            if (p["base_id"], p.get("arm", "")) in chunk
            and (p["variant"] in open_variants
                 or (p["variant"] in NEUTRAL_VARIANTS and start < NEUTRAL_MAX_BASES))
        ]
        report.rows += _score_pairs(
            scorer, todo, intact_cache, concurrency=concurrency, progress=progress,
            done_offset=len(report.rows), total=len(pairs), pair_scorer=pair_scorer,
        )
        for variant in open_variants:
            outcomes = [
                (r.correct if r.correct is not None else 0.0)
                for r in report.rows if r.variant == variant
            ]
            decision, llr = sprt(outcomes, min_accuracy)
            decisions[variant] = {"decision": decision, "llr": round(llr, 3), "n": len(outcomes)}
    # An undecided variant stopped either at the cap or because the answer
    # pool had no more usable bases; the two call for different remedies.
    # Per variant: with every usable answer sampled, one that applies to 5 of
    # them ran out of pool even when another variant reached the cap. With
    # more usable answers than the cap, a higher --max-bases adds bases to
    # every variant, so that is the cap even for a variant with few pairs.
    # With a store, only answers with verified material can ever be scored.
    if store is not None:
        usable = sum(c["kept"] for c in by_arm.values())
    else:
        usable = sum(1 for r in _answer_pool(answers) if len(r["answer"]) > DEFAULT_MIN_CHARS)
    for variant, d in decisions.items():
        if d["decision"] == "continue":
            bases = len({(p["base_id"], p.get("arm", "")) for p in pairs if p["variant"] == variant})
            d["stopped_by"] = "cap" if bases >= max_bases or usable > max_bases else "pool"
    report.wall_seconds = time.time() - started
    return report
