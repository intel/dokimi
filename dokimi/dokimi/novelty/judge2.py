# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A second judge over a seeded subset of the graded runs: how far the verdicts reproduce.

Every rate in a novelty run rests on one judge. Regrading a fraction of the same responses
with another model gives the agreement figures a reader needs before quoting them:
lenient agreement, Cohen's kappa on the lenient collapse, and exact 6-way agreement.
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor

from dokimi.utils import InputError
from .client import LLMClient, same_client
from .measure import ERRORED, LENIENT_CORRECT, MeasureResult, _grade
from .provenance import vendor_of

DEFAULT_FRAC = 0.15


def check_judge2(judge2: LLMClient, answerer: LLMClient, frac: float, allow_self_judge: bool) -> None:
    """Refuse a second judge that is the answering model, or a fraction outside (0, 1], before any call.

    Raises:
        InputError: The second judge is the answerer without ``allow_self_judge``, or a bad ``frac``.
    """
    if not 0 < frac <= 1:
        raise InputError(f"--judge2-frac must be in (0, 1], got {frac}")
    if same_client(judge2, answerer) and not allow_self_judge:
        raise InputError(
            f"--judge2 {judge2.model!r} is the answering model; grading a response with the "
            "model that produced it inflates the score. Pass another --judge2, or "
            "--allow-self-judge to do it deliberately.")


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    """Cohen's kappa of two binary ratings; ``None`` when chance agreement is total."""
    n = len(a)
    if not n:
        return None
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return None if chance >= 1 else round((observed - chance) / (1 - chance), 4)


def regrade(result: MeasureResult, judge2: LLMClient, *, frac: float = DEFAULT_FRAC,
            seed: int = 0, concurrency: int = 5) -> dict:
    """Regrade ``frac`` of the graded runs (seeded) with ``judge2`` and compare the verdicts.

    A run either judge failed to grade is left out of the agreement figures and counted.
    """
    by_key = {(p.atom_id, p.kind, p.question): p for p in result.probes}
    graded = [r for r in result.runs
              if not r.errored and (r.atom_id, r.probe_kind, r.question) in by_key]
    k = min(len(graded), max(1, round(frac * len(graded)))) if graded else 0
    picked = random.Random(f"novelty-judge2-{seed}").sample(graded, k)

    def one(run) -> str:
        return _grade(by_key[(run.atom_id, run.probe_kind, run.question)], run.response, judge2)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        second = list(pool.map(one, picked))
    pairs = [(r.verdict, v) for r, v in zip(picked, second, strict=True) if v != ERRORED]
    return {
        "model": judge2.model,
        "vendor": vendor_of(judge2.model),
        "frac": frac,
        "seed": seed,
        "n_sampled": k,
        "n_errored": k - len(pairs),
        **agreement(pairs),
    }


def agreement(pairs: list[tuple[str, str]]) -> dict:
    """Lenient agreement, kappa on lenient, and exact verdict agreement of (first, second) verdicts."""
    n = len(pairs)
    first = [a in LENIENT_CORRECT for a, _ in pairs]
    second = [b in LENIENT_CORRECT for _, b in pairs]
    if not n:
        return {"n_compared": 0, "lenient_agreement": None, "kappa_lenient": None,
                "exact_agreement": None}
    return {
        "n_compared": n,
        "lenient_agreement": round(sum(x == y for x, y in zip(first, second, strict=True)) / n, 4),
        "kappa_lenient": cohen_kappa(first, second),
        "exact_agreement": round(sum(a == b for a, b in pairs) / n, 4),
    }
