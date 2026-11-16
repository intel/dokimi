# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Test-retest noise of the novelty score: the same items, run again with another seed.

A retest runs ``skill novelty analyze`` again on each item with a different ``--seed`` and a
fresh ``--cache-dir``, then compares the headline ``k0`` scores of the two runs. The
differences give the noise floor: a difference between two single-run scores smaller than
the floor is not evidence of a difference. The floor is ``NOISE_Z`` times the spread of the
paired differences; for two single runs of different items that spread is the same, since
both carry one run's noise each.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from dokimi.utils import InputError

NOISE_Z = 1.96  # two-sided 95%
SCORINGS = ("lenient", "strict")
FILE_NAME = "novelty.json"


def _score(artifact: dict, scoring: str) -> float | None:
    value = (artifact.get("k0") or {}).get(scoring)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def load_runs(path: Path) -> dict[str, dict]:
    """The novelty artifacts under ``path`` (a directory, searched recursively, or one file),
    keyed by the artifact ref. A ref that appears twice is ambiguous and refused."""
    files = [path] if path.is_file() else sorted(path.rglob(FILE_NAME))
    if not files:
        raise InputError(f"no {FILE_NAME} under {path}; point at `skill novelty analyze` output")
    runs: dict[str, dict] = {}
    for file in files:
        try:
            artifact = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InputError(f"cannot read {file}: {exc}") from exc
        ref = (artifact.get("artifact") or {}).get("ref")
        if not ref:
            raise InputError(f"{file} has no artifact ref; it is not a novelty artifact")
        if ref in runs:
            raise InputError(f"artifact {ref} appears twice under {path}; keep one run per item")
        runs[ref] = artifact
    return runs


def pair_runs(first: dict[str, dict], second: dict[str, dict], scoring: str) -> dict:
    """Pair the two runs item by item.

    Returns ``pairs`` (ref, first, second, seeds, models), ``unmatched`` (refs in one run
    only), ``unscored`` (refs with no usable score in one run or the other) and ``warnings``
    for a retest that does not vary what it should: the same seed, or another model.
    """
    pairs, unmatched, unscored, warnings = [], [], [], []
    for ref in sorted(first.keys() | second.keys()):
        if ref not in first or ref not in second:
            unmatched.append(ref)
            continue
        a, b = first[ref], second[ref]
        score_a, score_b = _score(a, scoring), _score(b, scoring)
        if score_a is None or score_b is None or a.get("partial") or b.get("partial"):
            unscored.append(ref)
            continue
        seed_a, seed_b = (
            (a.get("run_config") or {}).get("seed"),
            (b.get("run_config") or {}).get("seed"),
        )
        if seed_a is not None and seed_a == seed_b:
            warnings.append(f"{ref}: both runs used seed {seed_a}; a retest needs another seed")
        if a.get("model") != b.get("model"):
            warnings.append(
                f"{ref}: the runs used different models ({a.get('model')} and {b.get('model')})"
            )
        pairs.append(
            {
                "ref": ref,
                "first": score_a,
                "second": score_b,
                "seeds": [seed_a, seed_b],
                "models": [a.get("model"), b.get("model")],
            }
        )
    if unmatched:
        warnings.append(f"{len(unmatched)} item(s) appear in one run only and are left out")
    if unscored:
        warnings.append(f"{len(unscored)} item(s) have no usable {scoring} score or a partial run")
    return {"pairs": pairs, "unmatched": unmatched, "unscored": unscored, "warnings": warnings}


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    try:
        return round(statistics.correlation(xs, ys), 4)
    except statistics.StatisticsError:  # one run scored every item the same
        return None


def retest_summary(pairs: list[dict], scoring: str = "lenient") -> dict:
    """Noise statistics over paired retest scores.

    ``floor`` is ``NOISE_Z`` times the sample standard deviation of ``second - first``
    (``None`` below two items). ``comparisons_below_floor`` counts pairs of items whose
    first-run scores differ by less than the floor: those two scores are not told apart by
    one run each.
    """
    if scoring not in SCORINGS:
        raise InputError(f"unknown scoring {scoring!r}; known: {list(SCORINGS)}")
    first = [p["first"] for p in pairs]
    second = [p["second"] for p in pairs]
    diffs = [b - a for a, b in zip(first, second, strict=True)]
    n = len(pairs)
    sd = round(statistics.stdev(diffs), 4) if n >= 2 else None
    floor = round(NOISE_Z * sd, 4) if sd is not None else None
    below = []
    if floor is not None:
        for i in range(n):
            for j in range(i + 1, n):
                gap = abs(first[i] - first[j])
                if gap < floor:
                    below.append({"a": pairs[i]["ref"], "b": pairs[j]["ref"], "gap": round(gap, 4)})
    return {
        "scoring": scoring,
        "n_items": n,
        "mean_abs_diff": round(sum(abs(d) for d in diffs) / n, 4) if n else None,
        "mean_shift": round(sum(diffs) / n, 4) if n else None,
        "sd_diff": sd,
        "pearson_r": _pearson(first, second),
        "floor": floor,
        "n_comparisons": n * (n - 1) // 2,
        "n_below_floor": len(below),
        "below_floor": below,
        "items": [
            {
                "ref": p["ref"],
                "first": p["first"],
                "second": p["second"],
                "diff": round(p["second"] - p["first"], 4),
                "abs_diff": round(abs(p["second"] - p["first"]), 4),
            }
            for p in pairs
        ],
    }


def render_markdown(summary: dict, *, warnings: list[str]) -> str:
    """The retest as a report: the noise floor first, then each item and the warnings."""
    lines = ["# Novelty test-retest", ""]
    if summary["floor"] is None:
        lines.append("Noise floor not measured: it needs at least two items scored in both runs.")
    else:
        lines += [
            f"- Noise floor on `{summary['scoring']}` (single-run difference, 95%): "
            f"**±{summary['floor']:.3f}**. Two single-run scores closer than this are not told apart.",
            f"- Items: {summary['n_items']}. Pearson r between runs: {summary['pearson_r']}. "
            f"Mean |diff|: {summary['mean_abs_diff']:.3f}. Mean shift (second − first): "
            f"{summary['mean_shift']:+.3f}.",
            f"- Comparisons below the floor: {summary['n_below_floor']} of {summary['n_comparisons']}.",
            "- A shift applies to every item alike, so it does not reorder items; it does move a "
            "score against a score from another run.",
        ]
    lines += ["", "| item | run 1 | run 2 | diff |", "|---|---|---|---|"]
    for item in summary["items"]:
        lines.append(
            f"| `{item['ref']}` | {item['first']:.2f} | {item['second']:.2f} | "
            f"{item['diff']:+.2f} |"
        )
    if warnings:
        lines += ["", "Warnings:"] + [f"- {w}" for w in warnings]
    lines.append("")
    return "\n".join(lines)
