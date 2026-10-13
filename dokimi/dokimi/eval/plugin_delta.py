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


class PluginDeltaError(ValueError):
    """Raised when two artifacts are not a valid plugin-effect pair."""


def compare_plugin_runs(control: Dict[str, Any], plugin: Dict[str, Any]) -> Dict[str, Any]:
    """Return a plugin-effect artifact for two paired ``arms.v1`` artifacts.

    ``control`` is normally the no-plugin run (``plugin_set: none``), and
    ``plugin`` is the run with the runtime plugin enabled. The function refuses to
    compare artifacts that differ on any axis that would confound the plugin
    effect.
    """

    _validate_pair(control, plugin)
    arms = list(control.get("arms") or [])
    score_rows = _score_deltas(control, plugin, arms)
    cost_rows = _cost_deltas(control, plugin, arms)
    text_rows = _answer_text_deltas(control, plugin, arms)
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
        "warnings": _warnings(control, plugin),
    }


def _validate_pair(control: Dict[str, Any], plugin: Dict[str, Any]) -> None:
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
    if "evaluations" in control and "evaluations" not in plugin:
        raise PluginDeltaError("plugin artifact has no evaluations to pair with baseline")
    if "evaluations" in plugin and "evaluations" not in control:
        raise PluginDeltaError("baseline artifact has no evaluations to pair with plugin")
    _validate_question_pair(control, plugin, "answers")
    if "evaluations" in control:
        _validate_question_pair(control, plugin, "evaluations")
        for field in ("judge_model", "judge_provider"):
            if not control.get(field) or not plugin.get(field):
                raise PluginDeltaError(
                    f"cannot compute judged plugin delta without stamped {field} "
                    "in both artifacts"
                )
            if control.get(field) != plugin.get(field):
                raise PluginDeltaError(
                    f"cannot compute judged plugin delta across different {field}: "
                    f"{control.get(field)!r} vs {plugin.get(field)!r}"
                )
        # What the judge was shown. Unstamped runs predate the stamp and were all
        # judged on report snippets, so two of them pair; a stamped and an
        # unstamped one used different judge protocols and do not.
        from dokimi.eval.judge_context import PROTOCOL_MIXED

        if PROTOCOL_MIXED in (control.get("judge_context"), plugin.get("judge_context")):
            raise PluginDeltaError(
                "cannot compute judged plugin delta from a run whose scores mix stored "
                f"treatment material and report snippets (judge_context {PROTOCOL_MIXED!r})"
            )
        if control.get("judge_context") != plugin.get("judge_context"):
            raise PluginDeltaError(
                "cannot compute judged plugin delta across different judge_context: "
                f"{control.get('judge_context')!r} vs {plugin.get('judge_context')!r}"
            )


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


def _validate_question_pair(control: Dict[str, Any], plugin: Dict[str, Any], section: str) -> None:
    control_ids = _ordered_question_ids(control, section)
    plugin_ids = _ordered_question_ids(plugin, section)
    if control_ids != plugin_ids:
        raise PluginDeltaError(
            f"cannot compute plugin delta across different {section} question IDs/order: "
            f"{control_ids!r} vs {plugin_ids!r}"
        )


def _ordered_question_ids(artifact: Dict[str, Any], section: str) -> list[str]:
    return [str(row.get("question_id")) for row in artifact.get(section, [])]


def _score_deltas(control: Dict[str, Any], plugin: Dict[str, Any], arms: list[str]) -> dict[str, Any]:
    control_scores = _scores_by_question(control)
    plugin_scores = _scores_by_question(plugin)
    out: dict[str, Any] = {}
    for arm in arms:
        pairs: list[tuple[float, float]] = []
        for qid, scores in control_scores.items():
            if qid not in plugin_scores:
                continue
            a = _aggregate(scores.get(arm))
            b = _aggregate(plugin_scores[qid].get(arm))
            if a is not None and b is not None:
                pairs.append((a, b))
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


def _scores_by_question(artifact: Dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(ev.get("question_id")): (ev.get("scores") or {})
        for ev in artifact.get("evaluations", [])
    }


def _aggregate(score: Any) -> float | None:
    if isinstance(score, dict) and isinstance(score.get("aggregate"), (int, float)):
        return float(score["aggregate"])
    return None


def _answer_metrics(artifact: Dict[str, Any], arms: list[str]) -> dict[str, dict[str, float | None]]:
    values: dict[str, dict[str, list[float]]] = {
        arm: {"raw_answer_chars": [], "final_answer_chars": []}
        for arm in arms
    }
    for rec in artifact.get("answers", []):
        for arm in arms:
            arm_row = (rec.get("arms") or {}).get(arm)
            if not isinstance(arm_row, dict):
                continue
            metrics = arm_row.get("metrics") or {}
            for field in ("raw_answer_chars", "final_answer_chars"):
                val = metrics.get(field)
                if isinstance(val, (int, float)):
                    values[arm][field].append(float(val))
    out: dict[str, dict[str, float | None]] = {}
    for arm, fields in values.items():
        out[arm] = {
            field: (round(sum(vals) / len(vals), 4) if vals else None)
            for field, vals in fields.items()
        }
    return out


def _num_delta(a: Any, b: Any) -> float | None:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(float(b) - float(a), 6)
    return None
