# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Per-arm rollups over answer records: agentic tool use, cost, and latency.

Split out of :mod:`dokimi.eval.arm_runner` to keep that module under
the repo's 500-line ceiling. All are pure aggregations over the
records ``ArmRunner.run`` produced, read through :mod:`dokimi.records`.
Two are re-attached to ``ArmRunner`` as
``_summarize_agentic_usage`` / ``_cost_summary`` so existing call sites and
tests keep working.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from dokimi.records import RecordLike
from dokimi.records import records as as_records

#: Stamp on an agentic answer's ``tool_usage["counts"]``: its tool counts skip
#: calls that failed (``error`` in the transcript). Unstamped answers counted
#: every attempt, so their tool-use rates do not pair with stamped ones.
TOOL_COUNTS = "completed_calls.v1"
#: A summary whose arm mixes stamped and unstamped answers; pools with nothing.
TOOL_COUNTS_MIXED = TOOL_COUNTS + "+unstamped"


def _tool_counts_stamp(stamped: int, n: int) -> str | None:
    """:data:`TOOL_COUNTS` when every answer with ``tool_usage`` is stamped, None
    when none is. Answers without ``tool_usage`` (an arm that failed to prepare)
    made no calls under either protocol and do not count.
    """
    if stamped == 0:
        return None
    return TOOL_COUNTS if stamped == n else TOOL_COUNTS_MIXED


def summarize_agentic_usage(records: Iterable[RecordLike]) -> Dict[str, Any]:
    """Aggregate tool-use/discoverability signals from answer records.

    Each arm carries ``tool_counts`` (see :func:`_tool_counts_stamp`) unless no
    answer of the arm was stamped, the protocol of runs before the stamp existed.
    """
    stats: Dict[str, Dict[str, Any]] = {}
    for rec in as_records(records, source="answers", kind="answers"):
        for arm_name, answer in rec.answers.items():
            arm = answer.raw
            if not arm.get("agentic"):
                continue
            s = stats.setdefault(
                arm_name,
                {
                    "n": 0,
                    "tool_call_total": 0,
                    "tool_used_n": 0,
                    "iteration_total": 0,
                    "skill_offered_n": 0,
                    "skill_called_n": 0,
                    "skill_call_total": 0,
                    "usage_n": 0,
                    "stamped_n": 0,
                },
            )
            s["n"] += 1
            tool_calls = int(arm.get("tool_call_count") or 0)
            s["tool_call_total"] += tool_calls
            if tool_calls > 0:
                s["tool_used_n"] += 1
            s["iteration_total"] += int(arm.get("iterations") or 0)

            usage = arm.get("tool_usage") or {}
            if usage:
                s["usage_n"] += 1
                s["stamped_n"] += int(usage.get("counts") == TOOL_COUNTS)
            skill = usage.get("skill") or {}
            if skill.get("offered"):
                s["skill_offered_n"] += 1
            if skill.get("called"):
                s["skill_called_n"] += 1
            s["skill_call_total"] += int(skill.get("call_count") or 0)

    summary: Dict[str, Any] = {}
    for arm_name, s in stats.items():
        n = s["n"] or 1
        skill_offered_n = s["skill_offered_n"] or 0
        summary[arm_name] = {
            "n": s["n"],
            "avg_tool_calls": round(s["tool_call_total"] / n, 2),
            "tool_use_rate": round(s["tool_used_n"] / n, 3),
            "avg_iterations": round(s["iteration_total"] / n, 2),
            "skill_offered_n": skill_offered_n,
            "skill_called_n": s["skill_called_n"],
            "skill_load_rate": (
                round(s["skill_called_n"] / skill_offered_n, 3)
                if skill_offered_n else None
            ),
            "avg_skill_calls": (
                round(s["skill_call_total"] / skill_offered_n, 2)
                if skill_offered_n else None
            ),
        }
        stamp = _tool_counts_stamp(s["stamped_n"], s["usage_n"])
        if stamp is not None:
            summary[arm_name]["tool_counts"] = stamp
    return summary

SKILL_ARM_KINDS = ("skill_agent", "skill_catalog")


def _offers_skill(arm: Mapping[str, Any]) -> bool:
    """Whether an arm offered a skill tool, read from its tools or its arm kind.

    The kind covers a failure with no tool record (``prepare_failed``), and the
    sub-treatment kinds cover a combined arm.
    """
    if ((arm.get("tool_usage") or {}).get("skill") or {}).get("offered"):
        return True
    meta = arm.get("metadata") or {}
    kinds = [meta.get("arm_kind")] + [t.get("arm_kind") for t in meta.get("treatments") or []
                                      if isinstance(t, dict)]
    return any(k in SKILL_ARM_KINDS for k in kinds)


def summarize_skill_selection(records: Iterable[RecordLike]) -> Dict[str, Any]:
    """Score the skills each agentic arm loaded first against ``expected_skill``.

    Only records that carry an ``expected_skill`` key count. Its value is a skill
    name, a list of acceptable names (sibling skills that serve the same problem),
    or ``None`` — no skill should load. Arms that offered no skill are skipped.

    The skills loaded in the first turn that loaded any are one choice. ``recall``
    and ``precision`` (hits over questions where any skill loaded) are strict: a hit needs every one of them acceptable. ``recall_any`` (one
    acceptable skill among them), ``multi_load_rate`` and ``mean_first_turn_loads``
    show how far the strict figure depends on multi-loading.
    """
    stats: Dict[str, Dict[str, Any]] = {}
    for rec in as_records(records, source="answers", kind="answers"):
        if rec.expected_skills is None:
            continue
        ok = set(rec.expected_skills)
        for arm_name, answer in rec.answers.items():
            arm = answer.raw
            skill = (arm.get("tool_usage") or {}).get("skill") or {}
            # A failed run still belongs to a skill arm; dropping it silently
            # would score a 9-of-10 failure as 1/1 correct.
            if not _offers_skill(arm):
                continue
            failed = bool(arm.get("error"))
            s = stats.setdefault(arm_name, {
                "n": 0, "correct": 0, "pos": 0, "hit": 0, "misroute": 0,
                "neg": 0, "false_trigger": 0, "failed": 0, "confusion": {},
                "hit_any": 0, "loaded_n": 0, "multi": 0, "turn_loads": 0,
            })
            if failed:
                s["failed"] += 1
                continue
            first = skill.get("selected")
            # Skills loaded together in the first turn are one choice. It is a hit
            # only if every one of them is acceptable: crediting "the right one is
            # among them" scores loading the whole catalog as a perfect hit.
            turn = list(skill.get("first_turn_loaded") or ([first] if first else []))
            hit = bool(turn) and all(x in ok for x in turn)
            hit_any = any(x in ok for x in turn)
            s["n"] += 1
            if turn:
                s["loaded_n"] += 1
                s["multi"] += len(turn) > 1
                s["turn_loads"] += len(turn)
            if ok:
                s["pos"] += 1
                s["hit"] += hit
                s["hit_any"] += hit_any
                s["misroute"] += bool(turn) and not hit
            else:
                s["neg"] += 1
                s["false_trigger"] += bool(turn)
            s["correct"] += hit if ok else not turn
            key = "|".join(sorted(ok)) or "(none)"
            row = s["confusion"].setdefault(key, {})
            picked = ("+".join(turn) if len(turn) <= 3 else f"({len(turn)} skills)") if turn else "(none)"
            row[picked] = row.get(picked, 0) + 1

    def rate(a: int, b: int) -> Any:
        return round(a / b, 3) if b else None

    return {
        arm_name: {
            "n": s["n"],
            "n_positive": s["pos"],
            "n_negative": s["neg"],
            "n_failed": s["failed"],
            "selection_accuracy": rate(s["correct"], s["n"]),
            "recall": rate(s["hit"], s["pos"]),
            # Of the questions where the arm loaded a skill, the share where every
            # loaded skill was acceptable (misroutes and false triggers count against).
            "precision": rate(s["hit"], s["loaded_n"]),
            # Lenient: the right skill was among those loaded in the first turn.
            "recall_any": rate(s["hit_any"], s["pos"]),
            "multi_load_rate": rate(s["multi"], s["loaded_n"]),
            "mean_first_turn_loads": rate(s["turn_loads"], s["loaded_n"]),
            "misroute_rate": rate(s["misroute"], s["pos"]),
            "false_trigger_rate": rate(s["false_trigger"], s["neg"]),
            "confusion": s["confusion"],
        }
        for arm_name, s in stats.items()
    }


def cost_summary(records: Iterable[RecordLike]) -> Dict[str, Any]:
    """Per-arm rollup of cost, tokens, latency, TTFT, and cache-hit ratio.

    Aggregates the per-row ``metrics{}`` blocks. ``cost_usd`` is summed only
    over priced rows (``None`` rows are skipped); the cache-hit ratio guards
    against a zero prompt-token denominator. The exact ratio is provider-
    dependent — here it is read cache tokens / prompt tokens.
    """
    per_arm: Dict[str, Dict[str, Any]] = {}
    for rec in as_records(records, source="answers", kind="answers"):
        for arm_name, answer in rec.answers.items():
            m = answer.raw.get("metrics")
            if not isinstance(m, dict):
                continue
            acc = per_arm.setdefault(arm_name, {
                "n": 0, "total_llm_calls": 0,
                "cost_usd": 0.0, "cost_known_n": 0,
                "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                "cache_read_tokens": 0, "latency_sec": 0.0,
                "ttft_sec_sum": 0.0, "ttft_n": 0,
            })
            acc["n"] += 1
            n_calls = m.get("n_calls", 1)
            acc["total_llm_calls"] += n_calls
            if m.get("cost_usd") is not None:
                acc["cost_usd"] += m["cost_usd"]
                acc["cost_known_n"] += m.get("cost_known_calls", n_calls) or 0
            for k in ("prompt_tokens", "completion_tokens", "total_tokens",
                      "cache_read_tokens"):
                acc[k] += m.get(k, 0) or 0
            acc["latency_sec"] += m.get("latency_sec", 0.0) or 0.0
            if m.get("ttft_sec") is not None:
                acc["ttft_sec_sum"] += m["ttft_sec"]
                acc["ttft_n"] += 1

    out: Dict[str, Any] = {}
    for arm_name, acc in per_arm.items():
        n = acc["n"] or 1
        prompt = acc["prompt_tokens"]
        out[arm_name] = {
            "n": acc["n"],
            "total_cost_usd": (round(acc["cost_usd"], 6)
                               if acc["cost_known_n"] else None),
            # How many rows the cost total covers — when < n, the dollar
            # figure is a partial sum (some LLM calls had no litellm pricing).
            "cost_known_n": acc["cost_known_n"],
            "total_llm_calls": acc["total_llm_calls"],
            "prompt_tokens": acc["prompt_tokens"],
            "completion_tokens": acc["completion_tokens"],
            "total_tokens": acc["total_tokens"],
            "mean_latency_sec": round(acc["latency_sec"] / n, 4),
            "mean_ttft_sec": (round(acc["ttft_sec_sum"] / acc["ttft_n"], 4)
                              if acc["ttft_n"] else None),
            "cache_hit_ratio": (round(acc["cache_read_tokens"] / prompt, 4)
                                if prompt else None),
        }
    return out


def paired_delta(evaluations: Iterable[Any], baseline_arm: str, arm_name: str) -> Optional[float]:
    """The mean of ``arm - baseline`` over the questions that both arms scored; ``None`` if none.

    A question one arm errored on (or left unscored) is not in the delta at all: the
    difference of two unpaired means would compare different questions.
    """
    diffs: List[float] = []
    for ev in as_records(evaluations, source="evaluations", kind="evaluations"):
        base, arm = ev.score(baseline_arm), ev.score(arm_name)
        if base is not None and arm is not None:
            diffs.append(float(arm) - float(base))
    return round(sum(diffs) / len(diffs), 1) if diffs else None


def cell_values(evaluations: Iterable[Any], arm_names: Sequence[str]) -> Tuple[Dict[str, List[float]], Dict[str, int]]:
    """Per arm, the aggregates that count, and how many cells errored instead.

    An errored cell (the answer call failed, or the judge raised) has no aggregate, so it
    is left out of the mean and its ``n``; the errors are counted apart, not as scores.
    """
    values: Dict[str, List[float]] = {name: [] for name in arm_names}
    errors: Dict[str, int] = {name: 0 for name in arm_names}
    for ev in as_records(evaluations, source="evaluations", kind="evaluations"):
        for arm_name, cell in ev.scores.items():
            if (v := ev.score(arm_name)) is not None:
                values.setdefault(arm_name, []).append(float(v))
            elif cell is not None and cell.error:
                errors[arm_name] = errors.get(arm_name, 0) + 1
    return values, errors
