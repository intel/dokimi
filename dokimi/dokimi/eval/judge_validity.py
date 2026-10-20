# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Discriminative validity of the judge: can it order pairs we already know?

The judge produces 0-100 scores that decide whether a skill ships. Nothing in
the pipeline currently checks that those scores track answer quality at all — a
prompt edit or a model swap can quietly degrade the judge to a coin flip and
every downstream number still looks plausible.

This module closes that hole. It takes real answers, builds pairs whose correct
ordering is known by construction (:mod:`dokimi.eval.degrade`), and
reports **pairwise accuracy**: the fraction of pairs where the judge scored the
intact answer above its damaged copy. Ties count as 0.5, because a judge that
cannot separate the two has not made the call.

Two failure signatures worth naming, both of which this surfaces:

* **low accuracy** — the judge is ordering by something other than quality.
* **high tie rate** — the judge is saturated at the top of its scale, so good
  and damaged answers land on the same number. This is the common one, and it
  is invisible in a correlation study.

The threshold is a gate, not a report: ``passed`` is false when any graded
(non-neutral) degradation's accuracy falls below it (a pair the judge failed to
score counts as misordered), so this can run as a scheduled check against a
stored answer set and fail loudly.

Two things keep an accuracy number from meaning what it looks like, and both are
reported alongside it:

* **A character counter passes most of these.** ``len(answer)`` is not a judge,
  and on the original five variants it scores 1.00 on ``truncate_20``,
  ``strip_code`` and ``shuffle_sentences``. Accuracy on those variants is
  therefore not evidence about the judge. The length-only baseline is computed
  on the same pairs, for free, and printed next to every variant; a judge that
  does not beat it there has shown nothing.
* **A single run at 20 bases cannot resolve the gate.** One base is worth
  1/n accuracy — 0.053 at n=19 — so a variant measured at 0.82 against a 0.80
  threshold is one pair from failing, and run-to-run spread with sampling
  unpinned is about that size. Every variant therefore carries a Wilson 95%
  interval, and when that interval straddles the gate the result is reported as
  inconclusive with the number of bases that would settle it. (Wilson, not a
  percentile bootstrap: the bootstrap has zero width on an all-correct run, so a
  3-pair pass would read as conclusive.)

Fixed sample sizes waste calls in both directions — 20 bases leaves most
variants inconclusive, while a variant at 0.98 was settled after a handful —
so :func:`run_sequential` adds bases in batches and stops each variant as soon
as a sequential probability ratio test (Wald's SPRT) can call it PASS or FAIL
at the configured error rates.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from dokimi.eval.degrade import DEGRADATIONS, NEUTRAL_VARIANTS, build_pairs
from dokimi.eval.validity_scoring import (  # noqa: F401  (re-exported)
    NEUTRAL_TOLERANCE,
    PairScorer,
    Scorer,
    ValidityRow,
    _score_pairs,
)
from dokimi.eval.stats import _Z95, _wilson  # noqa: F401  (re-exported)

#: Below this, the judge is not usable for arm comparison. A coin flip is 0.50;
#: 0.80 leaves room for genuinely ambiguous pairs without accepting noise.
DEFAULT_MIN_ACCURACY = 0.80



def _accuracy_ci(values: Sequence[float]) -> Tuple[float, float]:
    """Wilson 95% interval for the mean of per-pair outcomes in {0, 0.5, 1}.

    A percentile bootstrap was used here first and it collapses to zero width
    whenever every outcome is identical, so 3/3 or 20/20 correct came out as
    [1.0, 1.0] and a run that small was reported as a *conclusive* pass. The
    Wilson interval keeps its width at the boundary (20/20 -> [0.84, 1.0],
    19/20 -> [0.76, 0.99]), which is the case a gate has to get right.

    Ties enter as half a success. An outcome in {0, 0.5, 1} has variance at most
    p(1-p) for its mean p, so treating the pairs as Bernoulli makes the interval
    slightly wider than the data needs, never narrower.
    """
    n = len(values)
    if not n:
        return (0.0, 0.0)
    return _wilson(sum(values) / n, n)


#: Search ceiling for `_pairs_for_resolution`; reaching it means "far more pairs
#: than anyone will run", which is reported as such rather than as "on the gate".
PAIRS_SEARCH_LIMIT = 1_000_000


def _pairs_for_resolution(p: float, n: int, gate: float, limit: int = PAIRS_SEARCH_LIMIT) -> Optional[int]:
    """Smallest pair count at which the same accuracy stops straddling the gate.

    Searched on the Wilson interval itself rather than estimated from its
    half-width: near 1.0 the interval is lopsided, and the side facing the gate
    is the one that decides. (A symmetric 1/sqrt(n) estimate asked for 12 pairs
    at 19/20 — fewer than the run already had — when 28 are needed.) Returns
    None when accuracy is exactly on the gate: that accuracy straddles at
    any n, so no count follows; only the next run's accuracy settles it.
    """
    if p == gate:
        return None

    def straddles(m: int) -> bool:
        low, high = _wilson(p, m)
        return low < gate <= high

    if not straddles(n):
        return n
    lo, hi = n, n
    while straddles(hi):
        if hi >= limit:
            # Not "on the gate" — just closer to it than any run will get.
            return limit
        lo, hi = hi, min(limit, hi * 2)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if straddles(mid):
            lo = mid
        else:
            hi = mid
    return hi


def _move(mean_drop: float) -> str:
    """The padded copy's mean move as printed, and its direction read off the printed figure."""
    move = round(-mean_drop, 1) + 0.0  # + 0.0 turns -0.0 into 0.0
    lean = "rewards" if move > 0 else "penalises" if move < 0 else "neither rewards nor penalises"
    return f"{move:+.1f} ({lean}"


class NoUsablePairs(ValueError):
    """The answers yield no pair to score (too short, all no-ops, no stored material).

    A user-facing condition, distinct from a ``ValueError`` raised by a bug.
    """


@dataclass
class ValidityReport:
    """Per-variant and overall validity of one judge configuration."""

    judge: str
    rows: List[ValidityRow] = field(default_factory=list)
    min_accuracy: float = DEFAULT_MIN_ACCURACY
    wall_seconds: float = 0.0
    n_noop: Dict[str, int] = field(default_factory=dict)
    seed: int = 0
    #: The variants asked for, so one that produced no pair at all is still reported.
    variants: List[str] = field(default_factory=list)
    #: Set by :func:`run_sequential`: per graded variant, its SPRT decision,
    #: log-likelihood ratio and the pairs it took. ``None`` for a fixed-n run.
    sequential: Optional[Dict[str, Dict[str, Any]]] = None
    #: What the judge was shown with each answer: ``none`` (the default; the
    #: gate's original setting) or ``stored`` (the arm's treatment material from
    #: the artifact's ``judge_materials`` — the setting arms runs score in).
    context: str = "none"
    #: With ``context="stored"``, eligible answers dropped because the file held
    #: no verified material for their arm, counted over the whole pool before
    #: ``--bases`` sampling; scoring them without it would mix the two settings.
    n_without_context: int = 0
    #: With ``context="stored"``, per arm: eligible answers kept (material
    #: verified) and dropped (none), before ``--bases`` sampling, and the bases
    #: sampled from the kept ones. In --sequential mode also ``scored``: the
    #: bases that got rows before the SPRT stopped (``sampled`` is the pool built).
    by_arm: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @property
    def scored(self) -> List[ValidityRow]:
        return [r for r in self.rows if r.correct is not None]

    @property
    def graded(self) -> List[ValidityRow]:
        """Scored rows whose correct outcome is "intact wins" — the gate's set."""
        return [r for r in self.scored if not r.neutral]

    @property
    def errors(self) -> List[ValidityRow]:
        return [r for r in self.rows if r.error]

    def per_variant(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        # Every variant with a row, so one whose every pair errored still shows up (with n = 0).
        for variant in sorted({r.variant for r in self.rows}):
            subset = [r for r in self.scored if r.variant == variant]
            n_errors = sum(1 for r in self.errors if r.variant == variant)
            n = len(subset)
            d = n or 1
            neutral = variant in NEUTRAL_VARIANTS
            correct = [r.correct or 0.0 for r in subset]
            accuracy = sum(correct) / d
            # The interval is taken over the gate's own view of the pairs, so
            # the two cannot disagree about where the threshold falls.
            gate_values = correct + [0.0] * n_errors
            gate_accuracy = sum(gate_values) / (n + n_errors or 1)
            ci_low, ci_high = _accuracy_ci(gate_values)
            length_accuracy = sum(
                r.length_correct for r in self.rows if r.variant == variant
            ) / (n + n_errors)
            stats: Dict[str, Any] = {
                "n": n,
                "n_errors": n_errors,
                "neutral": neutral,
                "accuracy": accuracy,
                # The gate counts an errored pair as misordered: an unscored pair is not evidence.
                "gate_accuracy": gate_accuracy,
                "accuracy_ci": [ci_low, ci_high],
                "length_accuracy": length_accuracy,
                # Against the number the table prints next to it, over the
                # same pairs: with errored pairs, `accuracy` (errors excluded)
                # can beat the baseline while the displayed gate_accuracy does not.
                "beats_length": gate_accuracy > length_accuracy,
                "tie_rate": sum(1 for r in subset if r.correct == 0.5) / d,
                "mean_drop": sum(
                    (r.intact_score or 0.0) - (r.degraded_score or 0.0) for r in subset
                )
                / d,
                "mean_intact": sum(r.intact_score or 0.0 for r in subset) / d,
                "mean_degraded": sum(r.degraded_score or 0.0 for r in subset) / d,
            }
            if neutral:
                # Graded against "did not move", and the gate ignores it. A
                # judge rewarding the padded copy shows up as unmoved_rate low
                # with accuracy below 0.5.
                stats["unmoved_rate"] = sum(r.unmoved or 0.0 for r in subset) / d
            elif self.sequential is not None:
                seq = self.sequential.get(variant, {"decision": "continue", "llr": 0.0})
                stats["sequential"] = seq
                # The test, not the interval, decides here: a variant stopped
                # early has a wide interval by design.
                stats["straddles_gate"] = seq["decision"] == "continue"
                stats["pairs_for_resolution"] = None
            else:
                stats["straddles_gate"] = ci_low < self.min_accuracy <= ci_high
                stats["pairs_for_resolution"] = (
                    _pairs_for_resolution(gate_accuracy, n + n_errors, self.min_accuracy)
                    if stats["straddles_gate"] else None
                )
            if "pairs_for_resolution" in stats:
                # The old name, kept for stored reports and their readers. The
                # value is a pair count. Each base gives at most one pair per
                # variant, so it is also the number of bases to ask for, plus
                # those the variant leaves unchanged (no-ops give no pair).
                stats["bases_for_resolution"] = stats["pairs_for_resolution"]
            out[variant] = stats
        return out

    def summary(self) -> Dict[str, Any]:
        graded = self.graded
        n = len(graded)
        correct = [r.correct or 0.0 for r in graded]
        n_graded_errors = sum(1 for r in self.errors if r.variant not in NEUTRAL_VARIANTS)
        accuracy = sum(correct) / n if n else 0.0
        tie_rate = sum(1 for r in graded if r.correct == 0.5) / n if n else 0.0
        gate_rows = [r for r in self.rows if not r.neutral]
        length_accuracy = (
            sum(r.length_correct for r in gate_rows) / len(gate_rows) if gate_rows else 0.0
        )
        per_variant = self.per_variant()
        gated = {k: v for k, v in per_variant.items() if not v["neutral"]}
        inconclusive = sorted(k for k, v in gated.items() if v["straddles_gate"])
        # A fail is settled by one variant whose whole interval sits under the
        # gate, whatever the others straddle.
        settled_fail = any(v["accuracy_ci"][1] < self.min_accuracy for v in gated.values())
        # The sequential test decides, not the interval: its FAIL settles the run
        # exactly as a whole-interval-below-gate variant does, so an open variant
        # beside it does not make the result inconclusive.
        settled_fail |= any(v.get("sequential", {}).get("decision") == "FAIL" for v in gated.values())
        # A requested variant with no pair (every degradation was a no-op) was
        # not measured: the gate says nothing about it, so a pass cannot be settled.
        measured = {r.variant for r in self.rows}
        unmeasured = sorted(set(self.variants or self.n_noop) - measured)
        summary = {
            "judge": self.judge,
            # Pooled over the graded variants only; neutral variants have no
            # "intact wins" target and would bias the pool either way.
            "n_pairs": n,
            "n_neutral_pairs": len(self.scored) - n,
            "n_errors": len(self.errors),
            "accuracy": accuracy,
            # The gate's view of the pool (an errored pair counts as wrong), so
            # the printed point estimate sits inside its own interval.
            "gate_accuracy": sum(correct) / (n + n_graded_errors) if (n + n_graded_errors) else 0.0,
            "accuracy_ci": list(_accuracy_ci(correct + [0.0] * n_graded_errors)),
            # The degradation draw this report was built from.
            "seed": self.seed,
            "length_accuracy": length_accuracy,
            "tie_rate": tie_rate,
            "min_accuracy": self.min_accuracy,
            # No pairs scored fails; so does any variant below the gate, which a pooled mean hides.
            "passed": bool(n) and all(_variant_passes(v, self.min_accuracy) for v in gated.values()),
            "mode": "sequential" if self.sequential is not None else "fixed",
            # A pass whose interval straddles the threshold is not reproducible:
            # the gate cannot tell the configuration from one that fails.
            "conclusive": bool(n) and (not (inconclusive or unmeasured) or settled_fail),
            "inconclusive_variants": inconclusive,
            "unmeasured_variants": unmeasured,
            "per_variant": per_variant,
            "n_noop": dict(sorted(self.n_noop.items())),
            "context": self.context,
            "n_without_context": self.n_without_context,
            "by_arm": {a: dict(c) for a, c in sorted(self.by_arm.items())},
            "wall_seconds": self.wall_seconds,
        }
        if self.sequential is not None:
            from dokimi.eval.judge_sequential import SPRT_ALPHA, SPRT_BETA, sprt_bounds

            # The hypotheses actually tested, so the printed error rates are the real ones.
            p0, p1 = sprt_bounds(self.min_accuracy)
            summary["sprt"] = {"p0": p0, "p1": p1, "alpha": SPRT_ALPHA, "beta": SPRT_BETA}
        return summary

    def to_dict(self) -> Dict[str, Any]:
        return {"summary": self.summary(), "rows": [r.to_dict() for r in self.rows]}

    def _format_inconclusive(self, s: Dict[str, Any]) -> List[str]:
        lines = ["", "  INCONCLUSIVE — these intervals straddle the gate, so this",
                 "  run cannot tell a pass from a fail on them:"]
        for variant in s["inconclusive_variants"]:
            v = s["per_variant"][variant]
            if "sequential" in v:
                why = (
                    "answer pool exhausted — more answers"
                    + (" with verified material" if s.get("n_without_context") else "")
                    + ", not a higher --max-bases, would settle it"
                    if v["sequential"].get("stopped_by") == "pool"
                    else "cap reached — true accuracy is inside the gate's indifference band"
                )
                lines.append(
                    f"    {variant:20s} {v['gate_accuracy']:.2f} after {int(v['n'] + v['n_errors'])} pairs ({why})"
                )
                continue
            need = v["pairs_for_resolution"]
            lines.append(
                f"    {variant:20s} {v['gate_accuracy']:.2f} vs gate {s['min_accuracy']:.2f}"
                + (f" — needs {'over ' if need >= PAIRS_SEARCH_LIMIT else '~'}{need} pairs "
                   f"({need / max(1, int(v['n']) + int(v['n_errors'])):.1f}x this run)"
                   if need else " — on the gate exactly; add pairs, no count follows from a zero margin")
            )
        return lines

    def format_summary(self) -> str:
        s = self.summary()
        ci = s["accuracy_ci"]
        lines = [
            f"Judge validity — {s['judge']}",
            f"  pairs scored : {s['n_pairs']} graded"
            + (f" + {s['n_neutral_pairs']} neutral" if s["n_neutral_pairs"] else "")
            + (f"  (errors: {s['n_errors']})" if s["n_errors"] else ""),
            f"  accuracy     : {s['gate_accuracy']:.3f}  95% CI [{ci[0]:.3f}, {ci[1]:.3f}]"
            f"   (gate ≥ {s['min_accuracy']:.2f})"
            + (f"  [{s['accuracy']:.3f} with errored pairs left out]" if s["n_errors"] else ""),
            f"  length-only  : {s['length_accuracy']:.3f}   (len(answer) alone — not a judge)",
            f"  tie rate     : {s['tie_rate']:.3f}   (saturated scale if high)",
            f"  no-op pairs  : {sum(s['n_noop'].values())} dropped (degradation left the text unchanged)",
            *(["  context      : stored treatment material (as the arms run judged)"
               + (f"; {s['n_without_context']} answers without verified material "
                  "(counted before sampling)"
                  if s["n_without_context"] else "")] if s.get("context") == "stored" else []),
            *([f"  per arm      : {a}: {c['kept']} kept, {c['dropped']} dropped, "
               + (f"{c['scored']} bases scored" if "scored" in c
                  else f"{c.get('sampled', 0)} bases sampled")
               for a, c in s["by_arm"].items()] if s.get("context") == "stored" else []),
        ]
        if s["mode"] == "sequential":
            t = s["sprt"]
            lines.append(
                f"  mode         : sequential (SPRT {t['p0']:.3g} vs {t['p1']:.3g}, "
                f"alpha=beta={t['alpha']}) — each variant stops when decided"
            )
        lines += [
            "",
            f"  {'degradation':20s} {'n':>4s} {'err':>4s} {'gate':>6s} {'95% CI':>13s} "
            f"{'len':>6s} {'ties':>6s} {'drop':>7s} {'intact':>7s} {'damaged':>8s}",
        ]
        for variant, v in s["per_variant"].items():
            flag = "  neutral" if v["neutral"] else ("" if v["beats_length"] else "  <= length")
            if "sequential" in v:
                flag = f"  {v['sequential']['decision']:8s}" + flag
            vci = v["accuracy_ci"]
            lines.append(
                f"  {variant:20s} {int(v['n']):4d} {int(v['n_errors']):4d} "
                f"{v['gate_accuracy']:6.2f} [{vci[0]:5.2f},{vci[1]:5.2f}] "
                f"{v['length_accuracy']:6.2f} {v['tie_rate']:6.2f} {v['mean_drop']:+7.1f} "
                f"{v['mean_intact']:7.1f} {v['mean_degraded']:8.1f}{flag}"
            )
        for variant in s["unmeasured_variants"]:
            lines.append(f"  {variant:20s} {0:4d}  not measured: no pair, {s['n_noop'].get(variant, 0)} no-op")
        neutral = {k: v for k, v in s["per_variant"].items() if v["neutral"]}
        if neutral:
            lines.append("")
            for variant, v in neutral.items():
                lines.append(
                    f"  {variant}: correct outcome is no movement — "
                    f"unmoved {v['unmoved_rate']:.2f}, mean move {_move(v['mean_drop'])} the longer copy). "
                    "Not gated."
                )
        if s["inconclusive_variants"]:
            lines.extend(self._format_inconclusive(s))
        lines.append("")
        result = "PASS" if s["passed"] else "FAIL"
        if not s["n_pairs"]:
            result += " (no graded pair was scored)"
        elif not s["conclusive"]:
            result += " (inconclusive — see above)"
        lines.append("  RESULT: " + result)
        return "\n".join(lines)


def _variant_passes(v: Dict[str, Any], min_accuracy: float) -> bool:
    """One gated variant's verdict. A sequential FAIL fails even above the gate."""
    if v.get("sequential", {}).get("decision") == "FAIL":
        return False
    if v.get("sequential", {}).get("decision") == "PASS":
        return True
    return v["gate_accuracy"] >= min_accuracy


def evaluate_pairs(
    scorer: Scorer,
    pairs: Sequence[Dict[str, Any]],
    *,
    judge: str = "unknown",
    min_accuracy: float = DEFAULT_MIN_ACCURACY,
    seed: int = 0,
    progress: Optional[Callable[[int, int], None]] = None,
    concurrency: int = 1,
    pair_scorer: Optional[PairScorer] = None,
) -> ValidityReport:
    """Score every pair and report validity.

    The intact answer is scored once per (base_id, arm) and reused across that
    base's variants — the damaged copies are what differ, so re-scoring the
    intact member per variant would multiply the cost for nothing.
    """
    report = ValidityReport(judge=judge, min_accuracy=min_accuracy, seed=seed)
    started = time.time()
    report.rows = _score_pairs(
        scorer, pairs, {}, concurrency=concurrency, progress=progress, pair_scorer=pair_scorer
    )
    report.wall_seconds = time.time() - started
    return report


def judge_scorer(judge: Any, *, draw: int = 0) -> Scorer:
    """Adapt a :class:`~dokimi.eval.judge.Judge` to :data:`Scorer`.

    Ground truth is deliberately not passed: the pair differs only in the
    answer, and a grounded prompt would let the judge score the damaged copy
    against the excerpt rather than judge the answer itself.
    """

    def score(library: str, question: str, answer: str, context: Optional[str] = None) -> float:
        kwargs: Dict[str, Any] = {"library_name": library, "question": question, "answer": answer}
        if draw:
            kwargs["draw"] = draw
        if context is not None:
            kwargs["context"] = context
        return float(judge.score_answer(**kwargs)["aggregate"])

    return score


def require_context_scorer(scorer: Callable[..., Any]) -> None:
    """With a store, pairs are scored with ``context=``: refuse a scorer that cannot take it
    before any pair is built, rather than failing every pair mid-run."""
    try:
        params = inspect.signature(scorer).parameters
    except (TypeError, ValueError):  # a callable with no introspectable signature: let the call decide
        return
    takes_context = "context" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
    if not takes_context:
        raise NoUsablePairs(
            "The scorer does not accept a `context=` argument, so it cannot judge stored "
            "material: add a `context` parameter, or run without a store")


def refuse_unpaired_arms(
    pairs: Sequence[Dict[str, Any]], by_arm: Dict[str, Dict[str, int]], flag: str,
) -> None:
    """Refuse before scoring if an arm with kept material has no pair left: the
    sample can leave it out, or every one of its variants can be a no-op."""
    paired_arms = {p.get("arm", "") for p in pairs}
    starved = sorted(a for a, c in by_arm.items() if c.get("kept", 0) > 0 and a not in paired_arms)
    if starved:
        raise NoUsablePairs(
            f"Arm(s) {starved} have verified judge material but no base was scored, "
            f"so the gate would not cover them: raise {flag} or drop those arms"
        )


def run_validity(
    answers: Sequence[Dict[str, Any]],
    scorer: Scorer,
    *,
    judge: str = "unknown",
    n_bases: int = 20,
    seed: int = 0,
    variants: Optional[Sequence[str]] = None,
    library: str = "the library",
    min_accuracy: float = DEFAULT_MIN_ACCURACY,
    progress: Optional[Callable[[int, int], None]] = None,
    concurrency: int = 1,
    pair_scorer: Optional[PairScorer] = None,
    store: Optional[Any] = None,
) -> ValidityReport:
    """Build degradation pairs from ``answers`` and measure the judge on them.

    With *store* (``JudgeContextStore.from_artifact(data)``) each answer is
    judged with its arm's stored treatment material, as the arms run judged it.
    """
    if store is not None:
        require_context_scorer(scorer)
    noop: Dict[str, int] = {}
    variants = list(variants or DEGRADATIONS)
    by_arm: Dict[str, Dict[str, int]] = {}
    built = _build(answers, noop, n_bases=n_bases, seed=seed, variants=variants, library=library,
                   store=store, by_arm=by_arm)
    pairs = built.pairs
    if store is not None:
        refuse_unpaired_arms(pairs, by_arm, "--bases")
    report = evaluate_pairs(
        scorer, pairs, judge=judge, min_accuracy=min_accuracy, seed=seed, progress=progress,
        concurrency=concurrency, pair_scorer=pair_scorer,
    )
    report.n_noop = noop
    report.variants = variants
    report.context = "stored" if store is not None else "none"
    report.n_without_context = built.n_dropped
    report.by_arm = by_arm
    return report


def attach_stored_context(pairs: List[Dict[str, Any]], store: Any) -> List[Dict[str, Any]]:
    """Give each pair its arm's stored material.

    ``build_pairs`` has already dropped every base the store has no entry for,
    so each pair here resolves. An arm whose stored
    material is empty (``baseline``) is judged with the no-context marker, which
    is what the arms run showed its judge for it.
    """
    from dokimi.eval.judge_context import NO_CONTEXT

    kept = []
    for pair in pairs:
        text = store.get(pair["base_id"], pair.get("arm", ""))
        kept.append({**pair, "context": text or NO_CONTEXT})
    return kept


@dataclass(frozen=True)
class Built:
    """Output of :func:`_build`: the pairs, and how many answers had no stored material."""

    pairs: List[Dict[str, Any]]
    n_dropped: int = 0


def _build(answers, noop, *, n_bases, seed, variants, library, store=None,
           by_arm=None) -> Built:
    dropped_from_pairs: Dict[str, int] = {}
    arms: Dict[str, Dict[str, int]] = by_arm if by_arm is not None else {}
    pairs = build_pairs(
        answers,
        noop=noop,
        n_bases=n_bases,
        seed=seed,
        variants=variants or tuple(DEGRADATIONS),
        library=library,
        store=store,
        dropped=dropped_from_pairs,
        by_arm=arms,
    )
    if not pairs:
        if noop:
            raise NoUsablePairs(f"Every degradation left the answer unchanged (no-op pairs: {noop})")
        if store is not None and dropped_from_pairs.get("total", 0):
            raise NoUsablePairs(
                "No answer has verified stored judge material (judge_materials) — the file was "
                "written before arms runs kept it, or the material does not match its digests"
            )
        raise NoUsablePairs(
            "No usable answers found — need answers longer than the minimum "
            "length with a question_id and question text"
        )
    if store is not None:
        lost = sorted(a for a, c in arms.items() if c["dropped"] and not c["kept"])
        if lost:
            raise NoUsablePairs(
                f"Arm(s) {lost} have answers but no verified judge material, so the gate would "
                "not cover them: repair the artifact's judge_materials or drop those arms"
            )
        pairs = attach_stored_context(pairs, store)
        return Built(pairs, dropped_from_pairs.get("total", 0))
    return Built(pairs)
