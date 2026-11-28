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
from typing import TypeGuard

from dokimi.digest_entries import listed_entries
from dokimi.eval.correlation import pearson
from dokimi.novelty.measure import BASELINE_ARM
from dokimi.utils import InputError

NOISE_Z = 1.96  # two-sided 95%
SMALL_SAMPLE_N = 10  # below this many items the floor is a rough guide, not a bound
SCORINGS = ("lenient", "strict")
FILE_NAME = "novelty.json"
NAMED = 5  # refs named in a warning before the rest are counted
SMALL_SAMPLE_CAVEAT = (
    f"- Caveat: fewer than {SMALL_SAMPLE_N} items. The spread of a few differences is itself "
    "rough (two items give one degree of freedom): read the floor as an order of magnitude."
)


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _usable_score(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _score(artifact: dict, scoring: str) -> float | None:
    value = _dict(artifact.get("k0")).get(scoring)
    if not _usable_score(value):
        return None
    return float(value)


def _unprobed(artifact: dict, scoring: str) -> bool:
    """A run with no baseline measurement: no atom to probe, or every baseline grade errored.
    Its ``k0`` of 0.0 is then no measurement."""
    if _dict(artifact.get("atoms")).get("n_probed") == 0:
        return True
    arms = _dict(artifact.get("arms")).get(scoring)
    return isinstance(arms, dict) and BASELINE_ARM not in arms


def _config_value(config: dict, key: str):
    """``run_config[key]``; a tier compares by ``name`` and ``overrides``, since its
    ``forfeits`` follow how many atoms a run happened to extract."""
    value = config.get(key)
    if key == "tier" and isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "forfeits"}
    return value


def _named(refs: list[str]) -> str:
    more = f" and {len(refs) - NAMED} more" if len(refs) > NAMED else ""
    return ", ".join(refs[:NAMED]) + more


def _artifact_files(path: Path) -> list[Path]:
    """The ``FILE_NAME`` entries under ``path``, sorted; a directory that cannot be listed is an ``InputError``."""
    if not path.is_dir():
        return []  # mutation-ok[return-none]: the caller reads None as no files, as it reads []
    try:
        entries = listed_entries(path)
    except OSError as exc:  # its runs would be missing from the comparison, not reported
        raise InputError(f"cannot list {path}: {exc}") from exc
    return sorted(p for p in entries if p.match(FILE_NAME))


def load_runs(path: Path) -> dict[str, dict]:
    """The novelty artifacts under ``path`` (a directory, searched recursively, or one file),
    keyed by the artifact ref. A ref that appears twice is ambiguous and refused."""
    files = [path] if path.is_file() else _artifact_files(path)
    if not files:
        raise InputError(f"no {FILE_NAME} under {path}; point at `skill novelty analyze` output")
    runs: dict[str, dict] = {}
    for file in files:
        try:
            artifact = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InputError(f"cannot read {file}: {exc}") from exc
        except RecursionError as exc:  # json nests past the C stack limit on Python 3.11 to 3.13
            raise InputError(f"{file} is not a novelty artifact (nested too deep to read)") from exc
        if not isinstance(artifact, dict) or not isinstance(artifact.get("artifact"), dict):
            raise InputError(f"{file} is not a novelty artifact (no artifact block)")
        ref = artifact["artifact"].get("ref")
        if not isinstance(ref, str) or not ref:
            raise InputError(f"{file} has no non-empty string artifact ref; it is not a novelty artifact")
        if ref in runs:
            raise InputError(f"artifact {ref} appears twice under {path}; keep one run per item")
        runs[ref] = artifact
    return runs


def _judging(run: dict) -> dict:
    panel = _dict(run.get("judge_panel"))
    return {
        "judge_model": run.get("judge_model"),
        "judges": panel.get("judges"),
        "rule": panel.get("rule"),
    }


def _pair_warnings(ref: str, a: dict, b: dict) -> list[str]:
    """What makes two runs of one item a poor retest: the same seed, another model or judge,
    calls replayed from the response cache, or settings that differ beyond the seed."""
    config_a, config_b = _dict(a.get("run_config")), _dict(b.get("run_config"))
    seed = config_a.get("seed")
    warnings = []
    if seed is not None and seed == config_b.get("seed"):
        warnings.append(f"{ref}: both runs used seed {seed}; a retest needs another seed")
    if a.get("model") != b.get("model"):
        warnings.append(f"{ref}: the runs used different models ({a.get('model')} and {b.get('model')})")
    judging_a, judging_b = _judging(a), _judging(b)
    judged = [key for key in judging_a if judging_a[key] != judging_b[key]]
    if judged:
        warnings.append(f"{ref}: the runs were graded differently ({', '.join(judged)}); "
                        "a retest needs the same judge")
    for label, run in (("first", a), ("second", b)):
        replayed = _dict(run.get("cost")).get("replayed_calls") or 0
        if replayed:
            warnings.append(
                f"{ref}: the {label} run replayed {replayed} call(s) from the response "
                "cache (DOKIMI_RESPONSE_CACHE); a retest needs fresh calls"
            )
    differing = sorted(
        key for key in config_a.keys() | config_b.keys()
        if key != "seed" and _config_value(config_a, key) != _config_value(config_b, key)
    )
    if differing:
        warnings.append(
            f"{ref}: the runs used other settings ({', '.join(differing)}); "
            "two cells compare only if these match"
        )
    return warnings


def pair_runs(first: dict[str, dict], second: dict[str, dict], scoring: str) -> dict:
    """Pair the two runs item by item.

    Returns ``pairs`` (ref, first, second, seeds, models), ``unmatched`` (refs in one run
    only), ``unscored`` (refs without comparable scores) and ``warnings``
    for a retest that does not vary what it should: the same seed, another model, calls
    replayed from the response cache, or a ``run_config`` that differs beyond the seed.
    """
    pairs, unmatched, unscored, warnings = [], [], [], []
    unusable_scores = []
    for ref in sorted(first.keys() | second.keys()):
        if ref not in first or ref not in second:
            unmatched.append(ref)
            continue
        a, b = first[ref], second[ref]
        score_a, score_b = _score(a, scoring), _score(b, scoring)
        if (score_a is None or score_b is None or any(
                "partial" in run or run.get("status") == "partial" for run in (a, b))
                or _unprobed(a, scoring) or _unprobed(b, scoring)):
            unscored.append(ref)
            unusable_scores.append(ref)
            continue
        digest_a = _dict(a.get("artifact")).get("ref_digest")
        digest_b = _dict(b.get("artifact")).get("ref_digest")
        if digest_a != digest_b:
            if digest_a and digest_b:
                unscored.append(ref)
                warnings.append(f"{ref}: excluded because the content changed between runs "
                                f"(ref_digest {digest_a} and {digest_b})")
                continue
            warnings.append(f"{ref}: one run has no ref_digest to verify that the content matches")
        warnings.extend(_pair_warnings(ref, a, b))
        config_a, config_b = _dict(a.get("run_config")), _dict(b.get("run_config"))
        pairs.append(
            {
                "ref": ref,
                "first": score_a,
                "second": score_b,
                "seeds": [config_a.get("seed"), config_b.get("seed")],
                "models": [a.get("model"), b.get("model")],
            }
        )
    if unmatched:
        warnings.append(
            f"{len(unmatched)} item(s) appear in one run only and are left out: {_named(unmatched)}"
        )
    if unusable_scores:
        warnings.append(
            f"{len(unusable_scores)} item(s) have no usable {scoring} score, no probed atom or a partial run: "
            f"{_named(unusable_scores)}"
        )
    return {"pairs": pairs, "unmatched": unmatched, "unscored": unscored, "warnings": warnings}


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
    if not all(_usable_score(value) for scores in (first, second) for value in scores):
        raise InputError("retest pairs need numeric K0 scores between 0 and 1")
    diffs = [b - a for a, b in zip(first, second, strict=True)]
    n = len(pairs)
    sd = statistics.stdev(diffs) if n >= 2 else None
    floor = NOISE_Z * sd if sd is not None else None
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
        "sd_diff": round(sd, 4) if sd is not None else None,
        "pearson_r": pearson(first, second) if n >= 3 else None,
        "floor": round(floor, 4) if floor is not None else None,
        "small_sample": n < SMALL_SAMPLE_N,
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


def pearson_text(summary: dict) -> str:
    """The Pearson r as shown to a reader: the value, or why there is none."""
    if summary["pearson_r"] is not None:
        return str(summary["pearson_r"])
    if summary["n_items"] < 3:
        return "not measured (needs 3 items)"
    return "not defined (one run has the same score for every item)"


def render_markdown(summary: dict, *, warnings: list[str]) -> str:
    """The retest as a report: the noise floor first, then each item and the warnings."""
    lines = ["# Novelty test-retest", ""]
    if summary["floor"] is None:
        lines.append("Noise floor not measured: it needs at least two items scored in both runs.")
    else:
        lines += [
            f"- Noise floor on `{summary['scoring']}` (single-run difference, 95%): "
            f"**±{summary['floor']:.3f}**. Two single-run scores closer than this are not told apart.",
            f"- Items: {summary['n_items']}. Pearson r between runs: "
            f"{pearson_text(summary)}. "
            f"Mean |diff|: {summary['mean_abs_diff']:.3f}. Mean shift (second − first): "
            f"{summary['mean_shift']:+.3f}.",
            f"- Comparisons below the floor: {summary['n_below_floor']} of {summary['n_comparisons']}.",
            "- A shift applies to every item alike, so it does not reorder items; it does move a "
            "score against a score from another run.",
        ]
        if summary["small_sample"]:
            lines.append(SMALL_SAMPLE_CAVEAT)
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
