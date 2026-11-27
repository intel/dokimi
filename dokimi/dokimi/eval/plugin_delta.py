# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Compare paired plugin/no-plugin treatment-arm artifacts.

Plugin effects must be computed only between paired
cells that hold model, harness, suite, judge, and treatment arm fixed. This
module compares two ``arms.v1`` artifacts and emits that plugin-delta view
without rerunning the expensive answer/judge steps.
"""

from __future__ import annotations

from typing import Any, Dict

from dokimi.records import Record, RecordError, profile_prompt_digests, records


class PluginDeltaError(ValueError):
    """Raised when two artifacts are not a valid plugin-effect pair."""


def compare_plugin_runs(control: Dict[str, Any], plugin: Dict[str, Any]) -> Dict[str, Any]:
    """Return a plugin-effect artifact for two paired ``arms.v1`` artifacts.

    ``control`` is normally the no-plugin run (``plugin_set: none``), and
    ``plugin`` is the run with the runtime plugin enabled. The function refuses to
    compare artifacts that differ on any axis that would confound the plugin
    effect.
    """

    try:
        warnings = _validate_pair(control, plugin)
        arms = list(control.get("arms") or [])
        score_rows = _score_deltas(control, plugin, arms)
        cost_rows = _cost_deltas(control, plugin, arms)
        text_rows = _answer_text_deltas(control, plugin, arms)
    except RecordError as exc:
        raise PluginDeltaError(str(exc)) from exc
    return {
        "schema_version": "plugin_delta.v1",
        "library_name": control.get("library_name"),
        "model": control.get("model"),
        "provider": control.get("provider"),
        "harness": control.get("harness"),
        "judge_model": control.get("judge_model"),
        "judge_provider": control.get("judge_provider"),
        "baseline_plugin_set": control.get("plugin_set", "none"),
        "baseline_plugin_set_id": control.get("plugin_set_id"),
        "plugin_set": plugin.get("plugin_set", "none"),
        "plugin_set_id": plugin.get("plugin_set_id"),
        "arms": arms,
        "total_questions": control.get("total_questions"),
        "score_deltas": score_rows,
        "cost_deltas": cost_rows,
        "answer_text_deltas": text_rows,
        "warnings": warnings + _warnings(control, plugin),
    }


def _validate_run_config(control: Dict[str, Any], plugin: Dict[str, Any]) -> list[str]:
    """Budgets, sampling and session settings must match, as in ``replicate_key``."""
    before, after = control.get("run_config") or {}, plugin.get("run_config") or {}
    for field in ("session_turns", "turn_reminder"):
        if before.get(field) != after.get(field):
            raise PluginDeltaError(
                f"cannot compute plugin delta across different {field}: "
                f"{before.get(field)!r} vs {after.get(field)!r}"
            )
    if bool(before) != bool(after):
        return ["run_config was recorded by only one run; budgets and sampling were not compared"]
    for field in sorted((before.keys() | after.keys()) - {"seed", "seed_requested"}):
        if before.get(field) != after.get(field):
            raise PluginDeltaError(
                f"cannot compute plugin delta across different {field}: "
                f"{before.get(field)!r} vs {after.get(field)!r}"
            )
    if (before.get("seed") is None) != (after.get("seed") is None):
        raise PluginDeltaError(
            "cannot compute plugin delta between a seeded and an unseeded run: "
            f"seed {before.get('seed')!r} vs {after.get('seed')!r}"
        )
    return []


def _validate_run_pair(control: Dict[str, Any], plugin: Dict[str, Any]) -> None:
    for label, artifact in (("baseline", control), ("plugin", plugin)):
        if artifact.get("schema_version") != "arms.v1":
            raise PluginDeltaError(f"{label} artifact is not arms.v1")

    fixed_fields = (
        "library_name", "model", "provider", "harness", "arms",
        "baseline_arm", "total_questions",
    )
    for field in fixed_fields:
        if control.get(field) != plugin.get(field):
            raise PluginDeltaError(
                f"cannot compute plugin delta across different {field}: "
                f"{control.get(field)!r} vs {plugin.get(field)!r}"
            )
    if control.get("plugin_set") == plugin.get("plugin_set"):
        raise PluginDeltaError(
            "cannot compute plugin delta for identical plugin_set values "
            f"({control.get('plugin_set')!r})"
        )


def _validate_pair(control: Dict[str, Any], plugin: Dict[str, Any]) -> list[str]:
    _validate_run_pair(control, plugin)
    run_config_warnings = _validate_run_config(control, plugin)
    judged = "evaluations" in control
    if judged != ("evaluations" in plugin):
        with_scores, without_scores = ("baseline", "plugin") if judged else ("plugin", "baseline")
        raise PluginDeltaError(f"{without_scores} artifact has no evaluations to pair with {with_scores}")
    sections = ("answers", "evaluations") if judged else ("answers",)
    warnings = run_config_warnings + [
        f"{section} question text was not recorded for every pair"
        for section in sections if _validate_question_pair(control, plugin, section)
    ]
    if judged:
        warnings.extend(_validate_judge_pair(control, plugin))
    return warnings


def _validate_judge_pair(control: Dict[str, Any], plugin: Dict[str, Any]) -> list[str]:
    from dokimi.eval.judge_context import PROTOCOL_MIXED

    identity = ("judge_model", "judge_provider")
    settings = ("judge_prompt_sha256", "judge_temperature", "judge_resolved_models")
    for field in identity:
        if not control.get(field) or not plugin.get(field):
            raise PluginDeltaError(
                f"cannot compute judged plugin delta without stamped {field} in both artifacts"
            )
    if PROTOCOL_MIXED in (control.get("judge_context"), plugin.get("judge_context")):
        raise PluginDeltaError(
            "cannot compute judged plugin delta from a run whose scores mix stored "
            f"treatment material and report snippets (judge_context {PROTOCOL_MIXED!r})"
        )
    for field in (*identity, "judge_context", *settings):
        if control.get(field) != plugin.get(field):
            raise PluginDeltaError(
                f"cannot compute judged plugin delta across different {field}: "
                f"{control.get(field)!r} vs {plugin.get(field)!r}"
            )
    missing = set(settings) - (control.keys() & plugin.keys())
    if control.get("judge_prompt_sha256") is None:
        missing.add("judge_prompt_sha256")
    return [f"judge settings were not fully recorded ({', '.join(sorted(missing))})"] if missing else []


def _warnings(control: Dict[str, Any], plugin: Dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if control.get("plugin_set", "none") != "none":
        warnings.append(
            "baseline artifact plugin_set is not 'none'; result is plugin-set delta, "
            "not pure plugin-vs-no-plugin delta"
        )
    judge_a = control.get("judge_metrics")
    judge_b = plugin.get("judge_metrics")
    if bool(judge_a) != bool(judge_b):
        warnings.append("only one artifact carries judge_metrics")
    return warnings


def _validate_question_pair(control: Dict[str, Any], plugin: Dict[str, Any], section: str) -> bool:
    """Check alignment and report whether any pair lacks question text."""
    control_rows = _section(control, section)
    plugin_rows = _section(plugin, section)
    control_ids = [row.key for row in control_rows]
    plugin_ids = [row.key for row in plugin_rows]
    if control_ids != plugin_ids:
        raise PluginDeltaError(
            f"cannot compute plugin delta across different {section} question IDs/order: "
            f"{control_ids!r} vs {plugin_ids!r}"
        )
    check_judge_references = section == "answers" and "evaluations" in control
    for before, after in zip(control_rows, plugin_rows, strict=True):
        if before.question_text != after.question_text:
            raise PluginDeltaError(
                f"cannot compute plugin delta across different {section} question text for {before.key!r}"
            )
        if check_judge_references and before.ground_truth != after.ground_truth:
            raise PluginDeltaError(
                f"cannot compute plugin delta across different judge references for {before.key!r}"
            )
    if section == "answers":
        _validate_profiles(control_rows, plugin_rows, control.get("arms") or [])
    return any(not row.question_text for row in control_rows)


def _validate_profiles(control: list[Record], plugin: list[Record], arms: list[str]) -> None:
    for arm in arms:
        before = profile_prompt_digests(control, arm)
        after = profile_prompt_digests(plugin, arm)
        if before and after and before != after:
            raise PluginDeltaError(
                f"cannot compute plugin delta across different profile prompt digests for arm {arm!r}"
            )
    for before_row, after_row in zip(control, plugin, strict=True):
        for arm in arms:
            before = profile_prompt_digests((before_row,), arm)
            after = profile_prompt_digests((after_row,), arm)
            if before and after and before != after:
                raise PluginDeltaError(
                    f"cannot compute plugin delta across different profile prompt digests for arm {arm!r}"
                    f" on question {before_row.key!r}"
                )


def _section(artifact: Dict[str, Any], section: str) -> list[Record]:
    """The ``answers`` or ``evaluations`` records of one artifact."""
    return records(artifact.get(section, []), source=section, kind=section)


def _score_deltas(control: Dict[str, Any], plugin: Dict[str, Any], arms: list[str]) -> dict[str, Any]:
    control_scores = {r.key: r for r in _section(control, "evaluations")}
    plugin_scores = {r.key: r for r in _section(plugin, "evaluations")}
    out: dict[str, Any] = {}
    for arm in arms:
        pairs: list[tuple[float, float]] = []
        for qid, rec in control_scores.items():
            if qid not in plugin_scores:
                continue
            a = rec.score(arm)
            b = plugin_scores[qid].score(arm)
            if a is not None and b is not None:
                pairs.append((float(a), float(b)))
        if pairs:
            base_avg = sum(a for a, _ in pairs) / len(pairs)
            plugin_avg = sum(b for _, b in pairs) / len(pairs)
            out[arm] = {
                "n": len(pairs),
                "baseline_avg_aggregate": round(base_avg, 4),
                "plugin_avg_aggregate": round(plugin_avg, 4),
                "aggregate_delta": round(plugin_avg - base_avg, 4),
            }
        else:
            out[arm] = {
                "n": 0,
                "baseline_avg_aggregate": None,
                "plugin_avg_aggregate": None,
                "aggregate_delta": None,
            }
    return out


def _cost_deltas(control: Dict[str, Any], plugin: Dict[str, Any], arms: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    csum = control.get("cost_summary") or {}
    psum = plugin.get("cost_summary") or {}
    fields = (
        "total_cost_usd", "prompt_tokens", "completion_tokens", "total_tokens",
        "mean_latency_sec", "mean_ttft_sec",
    )
    for arm in arms:
        base = csum.get(arm) or {}
        cand = psum.get(arm) or {}
        row: dict[str, Any] = {}
        for field in fields:
            a = base.get(field)
            b = cand.get(field)
            row[field] = {"baseline": a, "plugin": b, "delta": _num_delta(a, b)}
        out[arm] = row
    return out


def _answer_text_deltas(control: Dict[str, Any], plugin: Dict[str, Any], arms: list[str]) -> dict[str, Any]:
    base = _answer_metrics(control, arms)
    cand = _answer_metrics(plugin, arms)
    out: dict[str, Any] = {}
    for arm in arms:
        row: dict[str, Any] = {}
        for field in ("raw_answer_chars", "final_answer_chars"):
            a = base.get(arm, {}).get(field)
            b = cand.get(arm, {}).get(field)
            row[field] = {"baseline_avg": a, "plugin_avg": b, "delta": _num_delta(a, b)}
        out[arm] = row
    return out


def _answer_metrics(artifact: Dict[str, Any], arms: list[str]) -> dict[str, dict[str, float | None]]:
    values: dict[str, dict[str, list[float]]] = {
        arm: {"raw_answer_chars": [], "final_answer_chars": []}
        for arm in arms
    }
    for rec in _section(artifact, "answers"):
        for arm in arms:
            answer = rec.answer(arm)
            if answer is None:
                continue
            metrics = answer.raw.get("metrics") or {}
            for field in ("raw_answer_chars", "final_answer_chars"):
                val = metrics.get(field)
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    values[arm][field].append(float(val))
    out: dict[str, dict[str, float | None]] = {}
    for arm, fields in values.items():
        out[arm] = {
            field: (round(sum(vals) / len(vals), 4) if vals else None)
            for field, vals in fields.items()
        }
    return out


def _num_delta(a: Any, b: Any) -> float | None:
    if (
        isinstance(a, (int, float)) and not isinstance(a, bool)
        and isinstance(b, (int, float)) and not isinstance(b, bool)
    ):
        return round(float(b) - float(a), 6)
    return None
