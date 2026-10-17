# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Multi-model comparison report generation.

Public API
----------
check_run_consistency(runs, group_name, treatment_arm=None)
    Validate a list of (run_id, run_data) pairs before comparison.
    Raises SystemExit on hard errors; returns a list of warning strings.

non_baseline_arms(run_data)
    The arm names in one artifact other than its baseline.

prompt_digests(run_data, arm)
    The agent-profile prompt digests stamped for one arm, for detecting an edited
    profile hiding behind an unchanged arm name.

extract_scores(run_data, force_treatment_arm=None)
    Extract per-question scores from an arms JSON artifact.

generate_combined_report(regular_runs, golden_runs, run_ids, out_path,
                         treatment_arm=None, correction="holm")
    Write a Markdown comparison report to *out_path*.

generate_section_report(runs, run_ids, section_name, treatment_arm=None,
                        correction="holm")
    Return a list of Markdown lines for one question-set section (regular or golden).
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dokimi.utils import question_id
from dokimi.eval.judge_context import PROTOCOL_MIXED as JUDGE_CONTEXT_MIXED
from dokimi.eval import stats as arm_stats

#: Mirrors :data:`dokimi.eval.stats.HAS_SCIPY` — the hypothesis tests
#: now live there, so probing for SciPy twice could only ever disagree. Still a
#: module-level name so reports can gate their statistics section on it.
HAS_SCIPY = arm_stats.HAS_SCIPY

try:
    from zoneinfo import ZoneInfo

    _TZ: ZoneInfo | None = ZoneInfo("America/Los_Angeles")
except ImportError:
    _TZ = None


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------


def load_run(path: str) -> dict[str, Any]:
    """Load a judged arms JSON file.  Raises SystemExit with a clear message on failure."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as exc:
        raise SystemExit(f"Error: File not found: {path}") from exc
    except PermissionError as exc:
        raise SystemExit(f"Error: Permission denied reading {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Error: Invalid JSON in {path}: {exc}") from exc


def normalize_difficulty(difficulty: Any) -> str:
    """Normalize difficulty to string format.

    Handles both string format ("easy"/"medium"/"hard") and numeric format (1/2/3).
    Returns normalized string or "unknown" if unrecognized.
    """
    if isinstance(difficulty, str):
        # Already string format - return as-is (lowercased for consistency)
        return difficulty.lower()
    elif isinstance(difficulty, int):
        # Numeric format: 1=easy, 2=medium, 3=hard
        mapping = {1: "easy", 2: "medium", 3: "hard"}
        return mapping.get(difficulty, "unknown")
    else:
        return "unknown"


# ---------------------------------------------------------------------------
# Consistency validation
# ---------------------------------------------------------------------------


def non_baseline_arms(run_data: dict) -> set[str]:
    """Arm names in *run_data* other than its baseline, for both artifact formats.

    Reads the top-level ``arms`` list when present (``arms.v1``) and otherwise falls back to
    the arm keys in the scored records, so a legacy artifact is checked on the same footing
    as a current one. The fallback unions over *every* record rather than reading the first:
    a judge failure drops an arm from a single record, and reading only that one would leave
    the run looking single-armed and quietly switch the consistency checks off.
    """
    baseline_arm = run_data.get("baseline_arm")
    arms = run_data.get("arms")
    if isinstance(arms, list) and arms:
        return {a for a in arms if isinstance(a, str) and a != baseline_arm}
    found: set[str] = set()
    for records, key in (
        (run_data.get("evaluations"), "scores"),
        (run_data.get("answers"), "arms"),
    ):
        for record in records or []:
            if isinstance(record, dict) and isinstance(record.get(key), dict):
                found |= {a for a in record[key] if a != baseline_arm}
    return found


def scored_arms(run_data: dict) -> set[str]:
    """The non-baseline arms that actually carry a score in *run_data*.

    This is the arm set the comparison is really built on, which is why it — and not
    :func:`non_baseline_arms` — decides whether two runs measure the same treatment.
    ``extract_scores`` selects, per record, the non-baseline arm that has a score dict, so
    a run declaring three arms whose third arm lost every judge call is scored on one arm
    and must still be compared as such. Deriving the check from the declared arm list
    instead made a run like that look ambiguous and switched the check off for the group.
    """
    baseline_arm = run_data.get("baseline_arm")
    found: set[str] = set()
    for records, key in (
        (run_data.get("evaluations"), "scores"),
        (run_data.get("answers"), "arms"),
    ):
        for record in records or []:
            if not isinstance(record, dict) or not isinstance(record.get(key), dict):
                continue
            found |= {
                arm for arm, value in record[key].items()
                if arm != baseline_arm and isinstance(value, dict)
            }
    return found


def prompt_digests(run_data: dict, arm: str) -> set[str]:
    """The ``profile_prompt_sha256`` values stamped for *arm* in one artifact.

    An agent-profile arm is named after the profile's frontmatter ``id``, so editing a
    profile in place produces cells that are indistinguishable by name while measuring
    a different prompt. The digest is the only thing that separates them.

    A combined arm stamps the same field one level down, under the entry of
    ``metadata["treatments"]`` belonging to its profile sub-treatment, so
    ``docs+profile:x`` has to be looked up in both shapes or the gate silently finds
    nothing to compare and reports the arm as consistent.
    """
    digests: set[str] = set()
    for answer in run_data.get("answers") or []:
        record = (answer.get("arms") or {}).get(arm)
        if not isinstance(record, dict):
            continue
        metadata = record.get("metadata") or {}
        candidates = [metadata]
        candidates += [t for t in (metadata.get("treatments") or []) if isinstance(t, dict)]
        for candidate in candidates:
            digest = candidate.get("profile_prompt_sha256")
            if isinstance(digest, str) and digest:
                digests.add(digest)
    return digests


def replicate_key(data: dict) -> tuple:
    """Configuration identity: models, judge prompt and temperature, run_config,
    harness, plugin set, cell and arm list.

    The seed value is the replicate index, so only whether one was pinned counts."""
    run_config = data.get("run_config") or {}
    return (
        data.get("model", ""),
        data.get("provider", ""),
        data.get("judge_model", ""),
        data.get("judge_provider", ""),
        data.get("judge_prompt_sha256"),
        data.get("judge_temperature"),
        tuple(sorted((k, repr(v)) for k, v in run_config.items() if k != "seed")),
        run_config.get("seed") is not None,
        data.get("harness"),
        data.get("plugin_set_id"),
        data.get("cell"),
        tuple(data.get("arms") or ()),
    )


def group_replicates(runs: list[tuple[str, dict]]) -> dict[tuple, list[str]]:
    """Map each configuration to the run_ids that measured it."""
    groups: dict[tuple, list[str]] = {}
    for run_id, data in runs:
        groups.setdefault(replicate_key(data), []).append(run_id)
    return groups


def replicate_label(run_ids: list[str]) -> str:
    """Label for a pooled group, derived from the caller's own run_ids.

    Uses their longest common prefix with any trailing separator or run-number
    fragment trimmed (``haiku45_run01..run10`` -> ``haiku45``), so the label
    echoes the naming the caller chose. Falls back to the first run_id when the
    ids share no usable prefix.
    """
    if len(run_ids) == 1:
        return run_ids[0]
    prefix = run_ids[0]
    for rid in run_ids[1:]:
        while prefix and not rid.startswith(prefix):
            prefix = prefix[:-1]
        if not prefix:
            break
    prefix = prefix.rstrip("0123456789").rstrip("_-. ")
    if prefix.endswith("run"):
        prefix = prefix[: -len("run")].rstrip("_-. ")
    stem = prefix or run_ids[0]
    return f"{stem}[n={len(run_ids)}]"


_SUMMED_COST_FIELDS = ("n", "total_cost_usd", "cost_known_n", "total_llm_calls",
                       "prompt_tokens", "completion_tokens", "total_tokens")


def pool_cost_summaries(summaries: list[dict | None]) -> dict:
    """Per-arm resource totals over replicates: counts summed, latency averaged.

    A field any replicate lacks stays None, so a partial total is never shown as a full one.
    """
    present = [s or {} for s in summaries]
    pooled: dict = {}
    for arm in dict.fromkeys(a for s in present for a in s):
        rows = [s.get(arm) or {} for s in present]
        fields = (*_SUMMED_COST_FIELDS, "mean_latency_sec", "cache_hit_ratio")
        known = {f: [r[f] for r in rows] for f in fields
                 if all(r.get(f) is not None for r in rows)}
        out: dict = {f: sum(known[f]) if f in known else None for f in _SUMMED_COST_FIELDS}
        latency = known.get("mean_latency_sec")
        out["mean_latency_sec"] = sum(latency) / len(latency) if latency else None
        ratios, prompts = known.get("cache_hit_ratio"), known.get("prompt_tokens")
        out["cache_hit_ratio"] = (sum(r * p for r, p in zip(ratios, prompts, strict=True)) / sum(prompts)
                                  if ratios and prompts and sum(prompts) else None)
        pooled[arm] = out
    return pooled


def pool_replicate_scores(
    all_run_scores: dict[str, list[dict]],
    run_ids: list[str],
) -> list[dict]:
    """Average per-question scores across replicates of one configuration.

    Averaging per question (rather than averaging each replicate's summary)
    keeps the paired structure the significance test depends on: a question is
    still one observation, just a less noisy one. With more than one replicate,
    a question is kept only if every replicate scored both arms, so every pooled
    row is a mean over the same number of samples.
    """
    per_q: dict[str, list[dict]] = {}
    order: list[str] = []
    for run_id in run_ids:
        for row in all_run_scores.get(run_id, []):
            qid = row["question_id"]
            if qid not in per_q:
                per_q[qid] = []
                order.append(qid)
            per_q[qid].append(row)

    pooled: list[dict] = []
    for qid in order:
        rows = per_q[qid]
        if len(run_ids) > 1:
            rows = [r for r in rows if r.get("with_docs") is not None and r.get("without_docs") is not None]
            if len(rows) < len(run_ids):
                continue
        merged = dict(rows[0])
        for field in ("with_docs", "without_docs", "delta"):
            vals = [r[field] for r in rows if r.get(field) is not None]
            merged[field] = (sum(vals) / len(vals)) if vals else None
        merged["n_replicates"] = len(rows)
        pooled.append(merged)
    return pooled


def _check_judge_context(runs: list[tuple[str, dict]], group_name: str) -> None:
    """Refuse to pool runs whose judge saw different evidence (see `judge_context.stamp_for`)."""
    stamps = {rid: data.get("judge_context") for rid, data in runs if "evaluations" in data}
    mixed = sorted(rid for rid, s in stamps.items() if s == JUDGE_CONTEXT_MIXED)
    if mixed:
        raise SystemExit(
            f"Error [{group_name}]: {', '.join(mixed)} judged some answers on the stored "
            "treatment material and some on report snippets (judge_context "
            f"{JUDGE_CONTEXT_MIXED!r}); such a run pools with nothing, not even another "
            "mixed one. Re-run answering and judging together, with unique question ids."
        )
    if len(set(stamps.values())) > 1:
        detail = ", ".join(f"{rid}={s or '<unstamped>'}" for rid, s in stamps.items())
        raise SystemExit(
            f"Error [{group_name}]: runs were judged under different judge_context "
            f"protocols ({detail}). The judge saw different evidence for the same "
            "answers, so the scores cannot be pooled. Re-run answering and judging "
            "together for the older runs: re-judging an answers file written before "
            "judge_materials only sees the 200-char report snippets, which is the "
            "unstamped protocol again."
        )


def check_run_consistency(
    runs: list[tuple[str, dict]],
    group_name: str,
    treatment_arm: str | None = None,
    pool_replicates: bool = False,
) -> list[str]:
    """Check runs for consistency problems; return warning strings.

    Hard errors (different ``baseline_arm``, different treatment arms, or
    duplicate configurations without ``pool_replicates``) raise ``SystemExit``
    immediately. Soft warnings (hash mismatch) are collected and returned.
    """
    if len(runs) < 2:
        return []

    warnings: list[str] = []

    # 1. baseline_arm must match across all runs in the group
    baseline_arms = {run_id: data.get("baseline_arm", "") for run_id, data in runs}
    unique_baselines = set(baseline_arms.values())
    if len(unique_baselines) > 1:
        detail = ", ".join(f"{rid}={arm}" for rid, arm in baseline_arms.items())
        raise SystemExit(
            f"Error [{group_name}]: runs have different baseline_arm values ({detail}). "
            "Comparing runs with different baselines produces meaningless deltas."
        )

    # 2. the treatment arm must match too, or the deltas answer different questions.
    # Without this the report happily puts one model's `profile:` delta beside another
    # model's `skill:` delta: `extract_scores` auto-selects each run's single
    # non-baseline arm, and nothing downstream names the arm it picked. An explicit
    # --treatment-arm is exempt — it pins one name, and extract_scores already fails
    # on a run that lacks it.
    if treatment_arm is None:
        # Gate on the arms that are actually *scored*, and never on how many runs are
        # legible: an earlier version required every run to resolve to exactly one arm
        # before comparing them, so a single run with two scored arms (or none) turned the
        # check off for the whole group and let a profile: delta pool with a skill: delta.
        per_run = {}
        for rid, data in runs:
            scored = scored_arms(data)
            # Fall back to the declared arm list for an artifact with no scored records at
            # all (answers not yet judged): it still names one treatment per run, and that
            # is enough to catch a confounded group before any judge spend.
            per_run[rid] = scored or non_baseline_arms(data)
            if not scored and (data.get("evaluations") or data.get("answers")):
                warnings.append(
                    f"Run '{rid}' has records but no scored non-baseline arm — it "
                    "contributes a baseline column and no delta."
                )
        distinct = set().union(*per_run.values()) if per_run else set()
        if len(distinct) > 1:
            detail = ", ".join(
                f"{rid}={'/'.join(sorted(arms)) if arms else '<none scored>'}"
                for rid, arms in per_run.items()
            )
            raise SystemExit(
                f"Error [{group_name}]: runs are scored on {len(distinct)} different "
                f"treatment arms ({detail}). Each run's delta would measure a different "
                "treatment, so the comparison is confounded. Compare runs that share one "
                "treatment, or pass --treatment-arm to pin the arm every run must be "
                "scored on. (A run listing more than one arm here is ambiguous on its own "
                "terms, and needs --treatment-arm regardless.)"
            )

    # 3. one arm name must mean one prompt. A `profile:` arm is named after the
    # profile's frontmatter id, so editing the profile in place leaves old and new cells
    # sharing a name while measuring different prompts — pooling them silently averages
    # two treatments. Runs predating the digest stamp carry none, and are left alone.
    shared_arm = treatment_arm
    if shared_arm is None:
        candidates = {a for _, data in runs for a in scored_arms(data)} or {
            a for _, data in runs for a in non_baseline_arms(data)
        }
        shared_arm = next(iter(candidates)) if len(candidates) == 1 else None
    if shared_arm is not None:
        by_run = {rid: prompt_digests(data, shared_arm) for rid, data in runs}
        all_digests = set().union(*by_run.values()) if by_run else set()
        if len(all_digests) > 1:
            detail = ", ".join(
                f"{rid}={'/'.join(sorted(d)[:1])[:19]}…" for rid, d in by_run.items() if d
            )
            raise SystemExit(
                f"Error [{group_name}]: arm '{shared_arm}' carries "
                f"{len(all_digests)} different prompt digests across runs ({detail}). "
                "The arm name comes from the profile's frontmatter id, so an edited "
                "profile keeps its name: these runs measured different prompts under one "
                "name and their scores cannot be pooled. Re-run the stale cells."
            )

    # 4. question_set_hash — warn if present in all runs but diverges
    hashes = {
        run_id: data["question_set_hash"]
        for run_id, data in runs
        if "question_set_hash" in data
    }
    if len(hashes) == len(runs) and len(set(hashes.values())) > 1:
        detail = ", ".join(f"{rid}={h[:8]}" for rid, h in hashes.items())
        warnings.append(
            f"Warning [{group_name}]: question_set_hash differs across runs ({detail}). "
            "Runs may be based on different question sets; common-question intersection "
            "will still be used but results may not be directly comparable."
        )

    # 4b. one judge protocol. `judge_context` names what the judge was shown about
    # each arm's material; runs predating the stamp were all judged on 200-char
    # report snippets, so two unstamped runs pool, but a stamped and an unstamped
    # run (or two different stamps) scored the same answers on different evidence.
    # Unjudged runs carry no stamp by construction and are left out.
    _check_judge_context(runs, group_name)

    # 5. duplicate configurations are replicates, not rival models. Ranking them
    #    against each other reports sampling noise as a model difference, so this
    #    is an error unless the caller opts into pooling. Checked per group, not
    #    only when *every* run matches — 3 replicates plus 1 other model is the
    #    likelier accident and previously passed silently.
    if not pool_replicates:
        dupes = {k: ids for k, ids in group_replicates(runs).items() if len(ids) > 1}
        if dupes:
            detail = "; ".join(
                f"{key[0]} ({key[1]}) judged by {key[2] or 'n/a'} -> runs {', '.join(ids)}"
                for key, ids in dupes.items()
            )
            raise SystemExit(
                f"Error [{group_name}]: {len(dupes)} configuration(s) appear more than "
                f"once ({detail}). These are replicates of one measurement, not rival "
                "models; ranking them against each other reports sampling noise as a "
                "model difference. Pass --pool-replicates to average them into one "
                "row per configuration, or pass one run per configuration."
            )

    return warnings


# ---------------------------------------------------------------------------
# Score extraction
# ---------------------------------------------------------------------------


def extract_scores(
    run_data: dict,
    force_treatment_arm: str | None = None,
) -> list[dict]:
    """Extract question-level scores from an arms artifact.

    Parameters
    ----------
    run_data:
        Parsed arms JSON artifact (``arms.v1`` schema or legacy format).
    force_treatment_arm:
        When given, only scores for this arm are used as the treatment.
        Raises ``SystemExit`` if the arm is absent.
        When ``None``, the single non-baseline arm is auto-selected; raises
        ``SystemExit`` if there is more than one non-baseline arm (ambiguous).
    """
    scores = []

    # New format: scores live inside top-level "evaluations"
    if "evaluations" in run_data:
        baseline_arm = run_data.get("baseline_arm")
        if not isinstance(baseline_arm, str) or not baseline_arm:
            return []

        for evaluation in run_data["evaluations"]:
            qid = question_id(evaluation)
            if qid is None:
                continue
            eval_scores = evaluation.get("scores", {})

            if not isinstance(eval_scores, dict) or not eval_scores:
                continue
            if baseline_arm not in eval_scores:
                continue

            baseline_data = eval_scores.get(baseline_arm, {})
            # Handle both dict (normal) and None (model failed)
            if isinstance(baseline_data, dict):
                baseline_score = baseline_data.get("aggregate")
            elif baseline_data is None:
                baseline_score = None  # Model failed to answer
            else:
                continue  # Unexpected format

            treatment_arms = [
                arm for arm in eval_scores
                if arm != baseline_arm and isinstance(eval_scores[arm], dict)
            ]

            if force_treatment_arm is not None:
                if force_treatment_arm not in eval_scores:
                    raise SystemExit(
                        f"Error: --treatment-arm '{force_treatment_arm}' not found in "
                        f"question '{qid}'. Available arms: {list(eval_scores.keys())}"
                    )
                target_arm: str | None = force_treatment_arm
            else:
                if len(treatment_arms) > 1:
                    raise SystemExit(
                        f"Error: run has multiple non-baseline arms {treatment_arms} in "
                        f"question '{qid}'. Use --treatment-arm to select one explicitly."
                    )
                target_arm = treatment_arms[0] if treatment_arms else None

            if target_arm is not None and isinstance(eval_scores.get(target_arm), dict):
                treatment_score = eval_scores[target_arm].get("aggregate")
                treatment_arm = target_arm
            else:
                treatment_score = None
                treatment_arm = None

            # A missing score is absent data, not a zero-quality answer: scoring
            # it 0.0 inflates the treatment delta. Carry it as None instead of
            # dropping the record, because the common-question intersection is
            # taken over question ids further down — a run whose scores were
            # dropped here would empty the intersection for every other run too.
            # Downstream aggregation filters None per metric.
            paired_delta = (
                treatment_score - baseline_score
                if treatment_score is not None and baseline_score is not None
                else None
            )
            scores.append(
                {
                    "question_id": qid,
                    "question_text": evaluation.get("question_text", ""),
                    "category": evaluation.get("category", ""),
                    "difficulty": normalize_difficulty(evaluation.get("difficulty", "unknown")),
                    "persona": evaluation.get("persona", ""),
                    "without_docs": baseline_score,
                    "with_docs": treatment_score,
                    "delta": paired_delta,
                    "treatment_arm": treatment_arm,
                }
            )

    # Legacy format: scores live inside per-answer "arms"
    else:
        baseline_arm = run_data.get("baseline_arm")
        if not isinstance(baseline_arm, str) or not baseline_arm:
            return []

        for answer in run_data.get("answers", []):
            qid = question_id(answer)
            if qid is None:
                continue
            arms = answer.get("arms", {})
            if not isinstance(arms, dict) or baseline_arm not in arms or len(arms) < 2:
                continue

            baseline_eval = arms.get(baseline_arm, {}).get("evaluation", {})
            treatment_arms = [a for a in arms if a != baseline_arm]

            if force_treatment_arm is not None:
                if force_treatment_arm not in arms:
                    raise SystemExit(
                        f"Error: --treatment-arm '{force_treatment_arm}' not found in "
                        f"question '{qid}'. Available arms: {list(arms.keys())}"
                    )
                treatment_arm = force_treatment_arm
            else:
                if len(treatment_arms) > 1:
                    raise SystemExit(
                        f"Error: run has multiple non-baseline arms {treatment_arms} in "
                        f"question '{qid}'. Use --treatment-arm to select one explicitly."
                    )
                treatment_arm = treatment_arms[0] if treatment_arms else None

            treatment_eval = (
                arms.get(treatment_arm, {}).get("evaluation", {}) if treatment_arm else {}
            )

            baseline_score = baseline_eval.get("aggregate_score")
            treatment_score = treatment_eval.get("aggregate_score")

            if baseline_score is not None and treatment_score is not None:
                scores.append(
                    {
                        "question_id": qid,
                        "question_text": answer.get("question_text", ""),
                        "category": answer.get("category", ""),
                        "difficulty": normalize_difficulty(answer.get("difficulty", "unknown")),
                        "persona": answer.get("persona", ""),
                        "without_docs": baseline_score,
                        "with_docs": treatment_score,
                        "delta": treatment_score - baseline_score,
                        "treatment_arm": treatment_arm,
                    }
                )

    return scores


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def _avg(lst: list[float]) -> float:
    return round(sum(lst) / len(lst), 1) if lst else 0.0


def _num_or_dash(v: float | None) -> str:
    """Render a metric, or an em dash when no usable score backs it."""
    return "—" if v is None else str(v)


def _rank_key(field: str):
    """Sort key that ranks runs with no usable value for *field* last."""
    def key(item):
        v = item[1].get(field)
        return (v is not None, v if v is not None else 0.0)

    return key


def _fmt_delta(d: float | None) -> str:
    if d is None:
        return "N/A"
    if isinstance(d, float):
        d = round(d, 1)
    return f"+{d}" if d > 0 else str(d)


def significance_test(
    with_scores: list[float], without_scores: list[float]
) -> dict | None:
    """Paired t-test, Wilcoxon, and Cohen's d_z (sample SD).  Requires scipy.

    Delegates to :func:`dokimi.eval.stats.paired_test` so this report
    and the arms report cannot drift apart on effect-size bands, p-value
    rounding, or the zero-variance guard (SciPy reports ``p == 0`` on constant
    deltas, which would read here as overwhelming significance).

    ``t_stat`` is kept for continuity with existing artifacts. It is the
    one-sample statistic on the deltas, which for paired data is the same
    quantity ``ttest_rel`` reported.
    """
    if not HAS_SCIPY or len(with_scores) < arm_stats.MIN_PAIRS_FOR_TEST:
        return None

    result = arm_stats.paired_test(with_scores, without_scores)
    if result is None:
        return None

    # This path applies no multiplicity correction, so leaving the keys present
    # would read as "corrected and unremarkable" rather than "not corrected".
    result.pop("p_adjusted", None)
    result.pop("significant_adjusted", None)

    if result["p_ttest"] is None:
        # No test ran; "not significant" would overstate what is known.
        result["significant"] = None

    deltas = [w - wo for w, wo in zip(with_scores, without_scores, strict=True)]
    sd = statistics.stdev(deltas)
    result["t_stat"] = (
        round(statistics.mean(deltas) / (sd / math.sqrt(len(deltas))), 3)
        if sd > 0
        else None
    )
    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _generated_stamp() -> str:
    """The report's creation time: Pacific time, or UTC without zoneinfo."""
    if _TZ is not None:
        return datetime.now(tz=_TZ).strftime('%Y-%m-%d %H:%M %Z')
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def generate_combined_report(
    regular_runs: list[tuple[str, dict]],
    golden_runs: list[tuple[str, dict]],
    run_ids: list[str],
    out_path: str,
    treatment_arm: str | None = None,
    correction: str = "holm",
    pool_replicates: bool = False,
    warnings: list[str] | None = None,
) -> None:
    """Write a Markdown comparison report to *out_path*.

    ``correction`` is the family-wise correction applied across the runs in each
    section — see :func:`dokimi.eval.stats.adjust_pvalues`.
    """
    lines: list[str] = []

    lines += [
        "#  Model Comparison Report",
        f"_Generated: {_generated_stamp()}_",
        "",
    ]

    # Consistency warnings belong in the report, not only on stderr — a shared
    # .md otherwise carries no trace of them.
    if warnings:
        lines += ["> **Consistency warnings**", ">"]
        lines += [f"> - {w}" for w in warnings]
        lines += [""]

    if regular_runs and golden_runs:
        lines += [
            "## Overview",
            "",
            "This report compares models across two question sets:",
            "- **Regular Questions**: standard questions covering getting started, compatibility, performance, and troubleshooting",
            "- **Golden Questions**: scenario-based questions derived from real issues",
            "",
            "---",
            "",
            "# Regular Questions Analysis",
            "",
        ]
        lines += generate_section_report(regular_runs, run_ids, "Regular", treatment_arm,
                                        correction,
                                        pool_replicates=pool_replicates)
        lines += [
            "",
            "---",
            "",
            "# Golden Questions Analysis",
            "",
        ]
        lines += generate_section_report(golden_runs, run_ids, "Golden", treatment_arm,
                                        correction,
                                        pool_replicates=pool_replicates)

    elif regular_runs:
        lines += generate_section_report(regular_runs, run_ids, "Regular", treatment_arm,
                                        correction,
                                        pool_replicates=pool_replicates)

    elif golden_runs:
        lines += generate_section_report(golden_runs, run_ids, "Golden", treatment_arm,
                                        correction,
                                        pool_replicates=pool_replicates)

    output = "\n".join(lines)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(output, encoding="utf-8")
    print(f"OK Report written to {out_path}")


def generate_section_report(
    runs: list[tuple[str, dict]],
    run_ids: list[str],
    section_name: str,
    treatment_arm: str | None = None,
    correction: str = "holm",
    pool_replicates: bool = False,
) -> list[str]:
    """Return Markdown lines for one question-set section.

    ``correction`` names the multiplicity correction applied across the runs
    compared in this section (``"holm"`` or ``"bh"``).

    With ``pool_replicates``, runs sharing a configuration are averaged per
    question into a single labelled row, so the report ranks configurations
    rather than individual samples of one configuration.
    """

    # Extract and intersect to common questions only
    all_run_scores_raw: dict[str, list[dict]] = {}
    for run_id, run_data in runs:
        all_run_scores_raw[run_id] = extract_scores(
            run_data, force_treatment_arm=treatment_arm
        )

    replicate_note: list[str] = []
    if pool_replicates:
        groups = group_replicates(runs)
        run_data_by_id = dict(runs)
        pooled_runs: list[tuple[str, dict]] = []
        pooled_ids: list[str] = []
        pooled_scores: dict[str, list[dict]] = {}
        for _key, ids in groups.items():
            present = [rid for rid in run_ids if rid in ids]
            if not present:
                continue
            label = replicate_label(present)
            while label in pooled_scores:          # keep labels unique
                label += "'"
            pooled_scores[label] = pool_replicate_scores(all_run_scores_raw, present)
            pooled_run = run_data_by_id[present[0]]
            if len(present) > 1:
                pooled_run = {**pooled_run, "cost_summary": pool_cost_summaries(
                    [run_data_by_id[rid].get("cost_summary") for rid in present])}
            pooled_runs.append((label, pooled_run))
            pooled_ids.append(label)
            if len(present) > 1:
                seen = {s["question_id"] for rid in present for s in all_run_scores_raw.get(rid, [])}
                dropped = len(seen) - len(pooled_scores[label])
                replicate_note.append(
                    f"- **{label}** pools {len(present)} replicates: {', '.join(present)}; "
                    f"{dropped} question(s) dropped for not being scored in every replicate"
                )
        runs = pooled_runs
        run_ids = pooled_ids
        all_run_scores_raw = pooled_scores

    question_sets = [
        set(s["question_id"] for s in scores)
        for scores in all_run_scores_raw.values()
    ]
    common_questions = set.intersection(*question_sets) if question_sets else set()

    # All metrics below use the common-question subset exclusively
    all_run_scores: dict[str, list[dict]] = {
        run_id: [
            s for s in all_run_scores_raw.get(run_id, [])
            if s["question_id"] in common_questions
        ]
        for run_id in run_ids
    }

    # The arm that was actually scored, taken from the extracted records rather than the
    # artifact header, so the label can never disagree with the numbers beside it. Named
    # explicitly because a `profile:` run and a `docs` run otherwise render identically:
    # a reader of the Markdown alone could not tell which treatment it measured.
    scored_arms = {
        s["treatment_arm"]
        for scores in all_run_scores.values()
        for s in scores
        if s.get("treatment_arm")
    }
    arm_label = next(iter(scored_arms)) if len(scored_arms) == 1 else "Context arm"

    lines: list[str] = []

    # --- Models Compared ---
    if replicate_note:
        lines += [
            "## Replicate Pooling",
            "",
            "Runs sharing a configuration were averaged per question, so each row "
            "below is one configuration rather than one sample of it. Each pooled "
            "score is a mean of k runs, so its spread and confidence intervals are "
            "narrower than a single run's: that reflects the k runs spent, not a "
            "sharper measurement.",
            "",
            *replicate_note,
            "",
        ]

    lines += [
        "## Models Compared",
        "| Run ID | Answer model | Judge model | Treatment arm |",
        "|---|---|---|---|",
    ]
    for run_id, run_data in runs:
        model = run_data.get("model", "unknown")
        provider = run_data.get("provider", "unknown")
        judge_model = run_data.get("judge_model", "same")
        judge_provider = run_data.get("judge_provider", provider)
        run_arms = sorted(
            {
                s["treatment_arm"]
                for s in all_run_scores.get(run_id, [])
                if s.get("treatment_arm")
            }
        )
        arm_cell = ", ".join(f"`{a}`" for a in run_arms) if run_arms else "— (baseline only)"
        lines.append(
            f"| **{run_id}** | {model} ({provider}) | {judge_model} ({judge_provider}) "
            f"| {arm_cell} |"
        )

    lines += [
        "",
        f"Common questions evaluated: **{len(common_questions)}**",
        "_All summary, significance, ranking, and head-to-head metrics use only this common question set._",
        "",
    ]

    # Detect report limitations (single-arm vs multi-arm)
    has_treatment = any(
        s.get("with_docs") is not None and s.get("delta") is not None
        for scores in all_run_scores.values()
        for s in scores
    )

    if not has_treatment:
        lines += [
            "> **⚠️ Report Limitation:** These runs contain only **baseline arm** (no treatment comparison).",
            "> ",
            "> **Missing from this report:**",
            "> - Context-arm vs Baseline delta analysis",
            "> - Statistical significance tests",
            "> - Head-to-head winner comparisons",
            "> ",
            "> **Available in this report:**",
            "> - Absolute baseline quality scores per model",
            "> - Resource usage metrics (cost, tokens, latency)",
            "> - Model ranking by absolute baseline quality",
            "> ",
            "> **To generate full comparison with delta analysis, add a treatment arm:**",
            "> ```bash",
            "> python cli.py arms run \\",
            ">   --arms \"baseline,docs\" \\",
            ">   --judge \\",
            ">   --judge-model claude-sonnet-5-5 \\",
            ">   [other parameters]",
            "> ```",
            "> ",
            "> Treatment arm options: `docs` (Context7 documentation), `skill:<path>` (local skill), `agent` (agentic doc retrieval).",
            "> ",
            "> See: `docs/benchmarking-and-comparison.md#treatment-arm-comparison`",
            "",
        ]

    # --- Resource Usage ---
    # Extract cost_summary from each run (baseline arm only for now)
    cost_data_available = any(
        run_data.get("cost_summary") for _, run_data in runs
    )
    if cost_data_available:
        lines += [
            "## Resource Usage (baseline arm)",
            "| Run | Total Cost | Prompt tok | Completion tok | Total tok | Avg latency/Q | Cache hit |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for run_id, run_data in runs:
            cost_summary = run_data.get("cost_summary", {})
            baseline_arm = run_data.get("baseline_arm", "baseline")
            cs = cost_summary.get(baseline_arm, {})

            # Extract metrics
            cost_usd = cs.get("total_cost_usd")
            cost_str = f"${cost_usd:.4f}" if cost_usd is not None else "—"
            if cost_usd is not None:
                cost_known = cs.get("cost_known_n")
                total_calls = cs.get("total_llm_calls", cs.get("n"))
                if cost_known is not None and total_calls is not None and cost_known < total_calls:
                    cost_str += f" ({cost_known}/{total_calls})"

            prompt_tok = cs.get("prompt_tokens")
            prompt_str = f"{prompt_tok:,}" if prompt_tok is not None else "—"

            completion_tok = cs.get("completion_tokens")
            completion_str = f"{completion_tok:,}" if completion_tok is not None else "—"

            total_tok = cs.get("total_tokens")
            total_str = f"{total_tok:,}" if total_tok is not None else "—"

            latency = cs.get("mean_latency_sec")
            latency_str = f"{latency:.2f}s" if latency is not None else "—"

            cache_ratio = cs.get("cache_hit_ratio")
            cache_str = f"{cache_ratio:.1%}" if cache_ratio is not None else "—"

            lines.append(
                f"| **{run_id}** | {cost_str} | {prompt_str} | {completion_str} | "
                f"{total_str} | {latency_str} | {cache_str} |"
            )
        if replicate_note:
            lines += ["", "_A pooled `[n=k]` row shows the total cost, calls and tokens of its k runs "
                          "and their mean latency._"]

        lines += [""]

    # --- Overall Summary ---
    lines += [
        "## Overall Summary",
        "_Delta is the mean of per-question paired differences. Scored pairs is the"
        " number of questions with a usable score in both arms; Delta, Improved and"
        " Degraded are computed over those only._",
        f"| Run | N | Scored pairs | {arm_label} | Baseline | Delta | Improved | Degraded |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    summary_stats: dict[str, dict] = {}
    for run_id in run_ids:
        scores = all_run_scores[run_id]

        # Handle both single-arm (baseline-only) and multi-arm runs
        with_docs_scores = [s["with_docs"] for s in scores if s["with_docs"] is not None]
        without_docs_scores = [s["without_docs"] for s in scores if s["without_docs"] is not None]

        # Per-run, distinct from the section-level has_treatment above: a single
        # run may lack a treatment arm while others in the comparison have one.
        run_has_treatment = bool(with_docs_scores)
        with_avg = _avg(with_docs_scores) if with_docs_scores else None
        without_avg = _avg(without_docs_scores) if without_docs_scores else None

        # Average the per-question deltas rather than differencing the two arm
        # means. The arm means are filtered independently, so when any question
        # is missing a score they cover different question subsets and their
        # difference is not a paired delta.
        paired_deltas = [s["delta"] for s in scores if s.get("delta") is not None]

        if run_has_treatment and paired_deltas:
            delta_avg = round(sum(paired_deltas) / len(paired_deltas), 1)
            improved = sum(1 for d in paired_deltas if d > 0)
            degraded = sum(1 for d in paired_deltas if d < 0)
        else:
            delta_avg = None
            improved = None
            degraded = None

        # Questions that reached the intersection but lack a usable pair.
        unpaired = len(scores) - len(paired_deltas)

        summary_stats[run_id] = {
            "n": len(scores),
            "n_paired": len(paired_deltas),
            "unpaired": unpaired,
            "with_avg": with_avg,
            "without_avg": without_avg,
            "delta_avg": delta_avg,
            "improved": improved,
            "degraded": degraded,
        }

        # An arm with no usable score prints as "—" rather than a numeric zero,
        # which would read as a legitimate score of 0/100.
        with_avg_str = "—" if with_avg is None else str(with_avg)
        without_avg_str = "—" if without_avg is None else str(without_avg)
        delta_str = "—" if delta_avg is None else f"**{_fmt_delta(delta_avg)}**"
        improved_str = "—" if improved is None else str(improved)
        degraded_str = "—" if degraded is None else str(degraded)
        # Make partial coverage visible: a delta over fewer pairs than questions
        # is weaker evidence than the question count alone suggests.
        scored_str = str(len(paired_deltas)) if not unpaired else f"{len(paired_deltas)} ({unpaired} unscored)"

        lines.append(
            f"| **{run_id}** | {len(scores)} | {scored_str} | {with_avg_str} | {without_avg_str} | "
            f"{delta_str} | {improved_str} | {degraded_str} |"
        )

    lines += [""]

    # --- Statistical Significance ---
    if HAS_SCIPY:
        lines += [
            "## Statistical Significance of Delta (α = 0.05)",
        ]

        if not has_treatment:
            lines += [
                "",
                "> **⚠️ Skipped:** Statistical tests require treatment arm (e.g., docs, skills).",
                "> Add `--arms \"baseline,docs\"` to enable delta analysis.",
                "",
            ]
        else:
            # Every run in the section is one test of the same "did the
            # treatment help?" question, so they form a family: with k runs,
            # the chance that at least one clears 0.05 by luck alone is far
            # above 0.05. Correct across the family before calling anything
            # significant, exactly as the arms report does across arms.
            sigs: dict[str, dict | None] = {}
            for run_id in run_ids:
                scores = all_run_scores[run_id]
                # Filter for paired scores: both baseline and treatment must be non-None
                paired = [(s["with_docs"], s["without_docs"]) for s in scores
                          if s["with_docs"] is not None and s["without_docs"] is not None]
                if paired:
                    with_paired, without_paired = zip(*paired, strict=True)
                    sigs[run_id] = significance_test(list(with_paired), list(without_paired))
                else:
                    sigs[run_id] = None

            tested = [sig for rid in run_ids if (sig := sigs[rid]) and sig["p_ttest"] is not None]
            adjusted = arm_stats.adjust_pvalues(
                [sig["p_ttest"] for sig in tested], method=correction
            )
            for sig, p_adj in zip(tested, adjusted, strict=True):
                sig["p_adjusted"] = p_adj
                # Same conjunction as the raw verdict, with the adjusted p in
                # place of the raw one.
                sig["significant_adjusted"] = bool(
                    p_adj is not None and p_adj < arm_stats.ALPHA
                    and (sig["p_wilcoxon"] is None or sig["p_wilcoxon"] < arm_stats.ALPHA)
                )

            label = "Holm-Bonferroni" if correction == "holm" else "Benjamini-Hochberg"
            lines += [
                f"_p-values are adjusted across the {len(tested)} run(s) compared here "
                f"({label}); the raw column is shown for reference. "
                "Significant = adjusted p < 0.05 and Wilcoxon < 0.05 (or unavailable)._",
                "",
                "| Run | Delta | p (t-test) | p (adjusted) | p (Wilcoxon) | Cohen's d_z "
                "| Effect | Significant? |",
                "|---|---:|---:|---:|---:|---:|---|---|",
            ]
            for run_id in run_ids:
                sig = sigs[run_id]
                if sig:
                    # p-values are None when no test could run (zero-variance
                    # deltas); "N/A" is honest where "None" would just look broken.
                    verdict = sig.get("significant_adjusted")
                    if sig["significant"] is None or verdict is None:
                        sig_mark = "N/A"
                    else:
                        sig_mark = "OK Yes" if verdict else "No"
                    p_ttest = sig["p_ttest"] if sig["p_ttest"] is not None else "N/A"
                    p_adj = sig.get("p_adjusted")
                    p_adj_str = p_adj if p_adj is not None else "N/A"
                    p_wilcox = sig["p_wilcoxon"] if sig["p_wilcoxon"] is not None else "N/A"
                    lines.append(
                        f"| **{run_id}** | {_fmt_delta(sig['delta_mean'])} | {p_ttest} | "
                        f"{p_adj_str} | {p_wilcox} | {_fmt_delta(sig['cohens_d'])} "
                        f"| {sig['effect']} | {sig_mark} |"
                    )
        lines += [""]

    # --- By Difficulty ---
    difficulty_groups: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for run_id in run_ids:
        for s in all_run_scores[run_id]:  # already common questions only
            difficulty_groups[s["difficulty"]][run_id].append(s)

    if difficulty_groups:
        # Adapt header to single-arm vs multi-arm scenario
        if has_treatment:
            header = "| Difficulty | N | " + " | ".join(
                f"{rid} context | {rid} Δ" for rid in run_ids
            ) + " |"
            separator = "|---|---:|" + "|".join([" :---: | :---: "] * len(run_ids)) + "|"
        else:
            header = "| Difficulty | N | " + " | ".join(
                f"{rid} baseline" for rid in run_ids
            ) + " |"
            separator = "|---|---:|" + "| :---: " * len(run_ids) + "|"

        lines += [
            "## By Difficulty (common questions only)",
            header,
            separator,
        ]
        for diff in ["easy", "beginner", "intermediate", "medium", "advanced", "hard"]:
            if diff not in difficulty_groups:
                continue
            run_data = difficulty_groups[diff]
            n = len(next(iter(run_data.values())))
            row = f"| {diff} | {n} |"
            for run_id in run_ids:
                sc = run_data.get(run_id, [])
                if sc:
                    with_scores = [s["with_docs"] for s in sc if s["with_docs"] is not None]
                    without_scores = [s["without_docs"] for s in sc if s["without_docs"] is not None]
                    # Same pairing rule as the overall summary: average the
                    # per-question deltas, never the difference of two means
                    # filtered over different question subsets.
                    diff_deltas = [s["delta"] for s in sc if s.get("delta") is not None]
                    if with_scores:
                        wa = _avg(with_scores)
                        delta_cell = (
                            _fmt_delta(round(sum(diff_deltas) / len(diff_deltas), 1))
                            if diff_deltas else "—"
                        )
                        row += f" {wa} | {delta_cell} |"
                    else:
                        # No treatment score in this bucket: report the baseline.
                        wo = _avg(without_scores) if without_scores else None
                        wo_cell = "—" if wo is None else f"{wo} (baseline)"
                        if has_treatment:
                            row += f" — | {wo_cell} |"
                        else:
                            row += f" {'—' if wo is None else wo} |"
                else:
                    if has_treatment:
                        row += " — | — |"
                    else:
                        row += " — |"
            lines.append(row)
        lines += [""]

    # --- Head-to-Head ---
    lines += [
        "## Head-to-Head (context arm, common questions)",
        "_On how many questions did each model achieve the highest absolute score (in the context arm)? This measures which model produces the best answers overall, not which benefits most from the treatment._",
        "",
    ]

    if not has_treatment:
        lines += [
            "",
            "> **⚠️ Skipped:** Head-to-head requires treatment arm scores.",
            "> Single-arm runs only have baseline scores.",
            "",
        ]
    else:
        lines += [
            "| Winner | Count | % |",
            "|---|---:|---:|",
        ]
        winner_counts: dict[str, int] = defaultdict(int)
        for qid in common_questions:
            question_scores = {}
            for run_id in run_ids:
                for s in all_run_scores[run_id]:
                    if s["question_id"] == qid and s["with_docs"] is not None:
                        question_scores[run_id] = s["with_docs"]
                        break
            if question_scores:
                max_score = max(question_scores.values())
                winners = [rid for rid, score in question_scores.items() if score == max_score]
                if len(winners) == 1:
                    winner_counts[winners[0]] += 1
                else:
                    winner_counts["tie"] += 1

        total = len(common_questions)
        for run_id in run_ids:
            count = winner_counts.get(run_id, 0)
            pct = round(100 * count / total) if total > 0 else 0
            lines.append(f"| **{run_id}** | {count} | {pct}% |")
        tie_count = winner_counts.get("tie", 0)
        lines.append(f"| tie | {tie_count} | {round(100 * tie_count / total) if total > 0 else 0}% |")
        lines += [""]

    # --- Context-Arm Benefit ---
    lines += [
        "## Context-Arm Benefit — Delta Comparison",
    ]

    if not has_treatment:
        lines += [
            "",
            "> **⚠️ Skipped:** Delta comparison requires treatment arm.",
            "> Single-arm runs cannot compute context benefit.",
            "",
        ]
    else:
        subtitle = (
            f"_Which model benefits more from the treatment arm `{arm_label}`?_"
            if len(scored_arms) == 1
            else "_Which model benefits more from the non-baseline treatment arm?_"
        )
        lines += [
            subtitle,
            "",
            "| Run | Avg Delta | Questions context helped | Questions context hurt |",
            "|---|---:|---:|---:|",
        ]
        for run_id in run_ids:
            st = summary_stats[run_id]
            # Percentages are over scored pairs, the same population the
            # improved/degraded counts come from.
            n_paired = st["n_paired"]
            if st["improved"] is None or not n_paired:
                lines.append(f"| **{run_id}** | — | — | — |")
                continue
            helped_pct = round(100 * st["improved"] / n_paired)
            hurt_pct = round(100 * st["degraded"] / n_paired)
            lines.append(
                f"| **{run_id}** | **{_fmt_delta(st['delta_avg'])}** | "
                f"{st['improved']} ({helped_pct}%) | {st['degraded']} ({hurt_pct}%) |"
            )
        lines += [""]

    # --- Model Ranking ---
    lines += [
        "## Model Ranking",
        "",
    ]

    if has_treatment:
        lines += [
            f"### By absolute quality ({arm_label})",
            f"| Rank | Run | {arm_label} avg |",
            "|---:|---|---:|",
        ]
        for rank, (run_id, st) in enumerate(
            sorted(summary_stats.items(), key=_rank_key("with_avg"), reverse=True), 1
        ):
            lines.append(f"| {rank} | **{run_id}** | {_num_or_dash(st['with_avg'])} |")
    else:
        lines += [
            "### By absolute quality (baseline)",
            "| Rank | Run | Baseline avg |",
            "|---:|---|---:|",
        ]
        for rank, (run_id, st) in enumerate(
            sorted(summary_stats.items(), key=_rank_key("without_avg"), reverse=True), 1
        ):
            lines.append(f"| {rank} | **{run_id}** | {_num_or_dash(st['without_avg'])} |")

    if has_treatment:
        lines += [
            "",
            "### By treatment utilisation (Delta)",
            "| Rank | Run | Delta |",
            "|---:|---|---:|",
        ]
        for rank, (run_id, st) in enumerate(
            sorted(summary_stats.items(), key=_rank_key("delta_avg"), reverse=True), 1
        ):
            lines.append(f"| {rank} | **{run_id}** | {_fmt_delta(st['delta_avg'])} |")
    else:
        lines += [
            "",
            "> **Note:** Treatment utilization ranking requires treatment arm.",
            "",
        ]

    lines += [""]
    return lines
