# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Render an N-arm treatment comparison as Markdown."""

from typing import Any, Dict, List

from dokimi.eval.arm_summary import TOOL_COUNTS
from dokimi.records import read
from dokimi.report import fmt


def _md_cell(text: str) -> str:
    """Make text safe for a single Markdown table cell."""
    return (text or "").replace("\n", " ").replace("|", r"\|")


_TTEST_MARK = "†"
_TTEST_NOTE = f"_{_TTEST_MARK} t-test p, and p (adj) its correction: the record predates the sign-flip test._"


def _is_ttest_p(result: Dict[str, Any]) -> bool:
    """True where an ``arms.json`` written before the sign-flip ``p`` existed holds a ``p_ttest`` instead."""
    return "p" not in result and result.get("p_ttest") is not None


def _raw_p(result: Dict[str, Any]) -> str:
    """The sign-flip ``p``, or an old record's ``p_ttest`` marked with :data:`_TTEST_MARK`."""
    if _is_ttest_p(result):
        return fmt.p_value(result["p_ttest"]) + _TTEST_MARK
    return fmt.p_value(result.get("p"))


def _ttest_note(results: List[Dict[str, Any]]) -> List[str]:
    """The note under a table that printed a ``p_ttest``, so it is not read as the sign-flip p."""
    return ["", _TTEST_NOTE] if any(_is_ttest_p(r) for r in results) else []


def _significance_lines(
    summary: Dict[str, Any], arms: List[str], baseline: str
) -> List[str]:
    """Paired significance table: every arm vs the one shared baseline."""
    rows = [
        (arm, summary["per_arm"][arm]["significance"])
        for arm in arms
        if arm != baseline
        and isinstance(summary.get("per_arm", {}).get(arm, {}).get("significance"), dict)
    ]
    if not rows:
        return []

    correction = next(
        (sig.get("correction") for _, sig in rows if sig.get("correction")), "holm"
    )
    label = "Holm-Bonferroni" if correction == "holm" else "Benjamini-Hochberg"

    lines = [
        "## Paired significance vs baseline",
        "",
        f"_Each arm is paired per question against `{baseline}`. "
        f"p-values are {label}-adjusted across the {len(rows)} treatment "
        "arm(s); **Sig.** uses the adjusted value. The 95% CI is not adjusted._",
        "",
        "| Arm | Δ (paired) | 95% CI | Cohen's d_z | Effect | p | p (adj) | Sig. | n |",
        "| --- | ---: | :---: | ---: | --- | ---: | ---: | :---: | ---: |",
    ]
    for arm, sig in rows:
        # Strictly the adjusted verdict. Falling back to the raw `significant`
        # when it is None would report an uncorrected claim under a column
        # header that promises correction.
        lines.append(
            f"| `{arm}` | {fmt.ci_end(sig.get('delta_mean'))} | "
            f"{fmt.ci(sig.get('ci_lo'), sig.get('ci_hi'))} | "
            f"{fmt.signed(sig.get('cohens_d'), 2)} | {sig.get('effect', '—')} | "
            f"{_raw_p(sig)} | {fmt.p_value(sig.get('p_adjusted'))} | "
            f"{fmt.verdict(sig.get('significant_adjusted'))} | {sig.get('n', 0)} |"
        )
    lines += _ttest_note([sig for _, sig in rows])
    lines.append("")
    return lines


def _interaction_lines(summary: Dict[str, Any]) -> List[str]:
    """Factorial interaction: does combining two treatments beat their sum?"""
    interactions = summary.get("interactions") or []
    if not interactions:
        return []

    lines = [
        "## Factorial interaction",
        "",
        "_Per-question contrast `combined - A - B + baseline`, i.e. how far the "
        "combined arm lands from the sum of the two individual gains. Negative "
        "means the treatments interfere; positive means they reinforce. All four "
        "cells come from the same question, so this is a paired contrast. "
        f"p-values are adjusted across the {len(interactions)} interaction "
        "contrast(s) — a family of their own, separate from the arm table above._",
        "",
        "| A | B | Δ A | Δ B | Additive expectation | Δ combined | Interaction | "
        "95% CI | p | p (adj) | Sig. | n |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | :---: | ---: | ---: | "
        ":---: | ---: |",
    ]
    for it in interactions:
        lines.append(
            f"| `{it.get('arm_a')}` | `{it.get('arm_b')}` | "
            f"{fmt.ci_end(it.get('delta_a'))} | {fmt.ci_end(it.get('delta_b'))} | "
            f"{fmt.ci_end(it.get('additive_expectation'))} | "
            f"{fmt.ci_end(it.get('delta_combined'))} | "
            f"**{fmt.ci_end(it.get('interaction'))}** | "
            f"{fmt.ci(it.get('ci_lo'), it.get('ci_hi'))} | "
            f"{_raw_p(it)} | {fmt.p_value(it.get('p_adjusted'))} | "
            f"{fmt.verdict(it.get('significant_adjusted'))} | {it.get('n', 0)} |"
        )
    lines += _ttest_note(interactions)
    lines.append("")
    return lines


def render_arms_report(data: Dict[str, Any]) -> str:
    """Render the artifact produced by ``ArmRunner.build_output`` as Markdown."""
    lines: List[str] = []
    lib = data.get("library_name", "?")
    arms = data.get("arms", [])
    baseline = data.get("baseline_arm", "baseline")

    lines.append(f"# Treatment-arm comparison — {lib}")
    lines.append("")
    lines.append(f"- Model: `{data.get('provider', '?')}/{data.get('model', '?')}`")
    lines.append(f"- Harness: `{data.get('harness', 'arms-runner')}`")
    if data.get("matrix_cell"):
        lines.append(f"- Matrix cell: `{data['matrix_cell']}`")
    lines.append(f"- Plugin set: `{data.get('plugin_set', 'none')}` (`{data.get('plugin_set_id', '?')}`)")
    lines.append(f"- Arms: {', '.join(f'`{a}`' for a in arms)}")
    lines.append(f"- Baseline arm: `{baseline}`")
    lines.append(f"- Questions: {data.get('total_questions', 0)}")
    lines.append("")

    summary = data.get("summary")
    if summary and summary.get("per_arm"):
        lines.append("## Summary (avg aggregate score, 0–100)")
        lines.append("")
        lines.append("| Arm | Avg | Δ vs baseline | n | Errors |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for arm in arms:
            stats = summary["per_arm"].get(arm, {})
            avg = stats.get("avg_aggregate")
            delta = stats.get("delta_vs_baseline")
            delta_s = "(baseline)" if arm == baseline else fmt.signed(delta)
            lines.append(f"| `{arm}` | {fmt.fixed(avg)} | {delta_s} | {stats.get('n', 0)} | {stats.get('errors', '—')} |")
        lines.append("")
        lines.extend(_significance_lines(summary, arms, baseline))
        lines.extend(_interaction_lines(summary))
    else:
        lines.append("_No evaluations available (answers were not judged)._")
        lines.append("")

    # Cost & latency: per-arm token/cost/latency rollup from the metrics blocks.
    cost_summary = data.get("cost_summary")
    if cost_summary:
        lines.append("## Cost & latency")
        lines.append("")
        lines.append("| Arm | Cost (USD) | Prompt tok | Completion tok | "
                     "Mean latency (s) | Mean TTFT (s) | Cache hit | n |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for arm in arms:
            cs = cost_summary.get(arm)
            if not cs:
                continue
            cost = cs.get("total_cost_usd")
            cost_s = fmt.usd(cost)
            # Flag a partial total when some rows lacked litellm pricing.
            known = cs.get("cost_known_n")
            n = cs.get("total_llm_calls", cs.get("n"))
            if cost is not None and known is not None and n is not None and known < n:
                cost_s += f" ({known}/{n})"
            lines.append(
                f"| `{arm}` | {cost_s} | {cs.get('prompt_tokens', 0)} | "
                f"{cs.get('completion_tokens', 0)} | {fmt.fixed(cs.get('mean_latency_sec'), 3)} | "
                f"{fmt.fixed(cs.get('mean_ttft_sec'), 3)} | {fmt.percent(cs.get('cache_hit_ratio'), 1)} | "
                f"{cs.get('n', 0)} |"
            )
        lines.append("")

    agentic_stats = data.get("agentic_usage_summary") or {}
    if not agentic_stats:
        # Backward-compatible fallback for older artifacts.
        fallback: Dict[str, Dict[str, Any]] = {}
        for rec in read(data, source="arms output", section="answers", required=False):
            for arm_name, answer in rec.answers.items():
                arm = answer.raw
                if arm.get("agentic"):
                    s = fallback.setdefault(arm_name, {"calls": [], "iters": []})
                    s["calls"].append(arm.get("tool_call_count", 0))
                    s["iters"].append(arm.get("iterations", 0))
        for arm_name, s in fallback.items():
            n = len(s["calls"]) or 1
            agentic_stats[arm_name] = {
                "n": len(s["calls"]),
                "avg_tool_calls": sum(s["calls"]) / n,
                "tool_use_rate": sum(1 for c in s["calls"] if c > 0) / n,
                "avg_iterations": sum(s["iters"]) / n,
                "skill_load_rate": None,
            }
    if agentic_stats:
        lines.append("## Agentic tool use")
        lines.append("")
        lines.append("| Arm | Tool-use rate | Avg tool calls | Skill-load rate | Avg iterations | n |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for arm_name, s in agentic_stats.items():
            lines.append(
                f"| `{arm_name}` | {fmt.percent(s.get('tool_use_rate', 0))} | "
                f"{fmt.fixed(s.get('avg_tool_calls', 0))} | {fmt.percent(s.get('skill_load_rate'))} | "
                f"{fmt.fixed(s.get('avg_iterations', 0))} | {s.get('n', 0)} |"
            )
        lines.append("")
        old = [arm for arm, s in agentic_stats.items() if s.get("tool_counts") != TOOL_COUNTS]
        if old:
            lines.append(
                "Tool counts of " + ", ".join(f"`{a}`" for a in old) + " include failed "
                "calls (all or some answers predate the `" + TOOL_COUNTS + "` stamp): "
                "do not compare their rates with stamped runs."
            )
            lines.append("")

    selection = data.get("skill_selection_summary") or {}
    if selection:
        lines.append("## Skill selection")
        lines.append("")
        lines.append(
            "Skills loaded in the first loading turn vs the question's `expected_skill`: "
            "a hit only if all are acceptable; *Recall (any)* counts one acceptable "
            "among them. A tool-calling proxy: it over-predicts what a real coding "
            "harness does."
        )
        lines.append("")
        lines.append("| Arm | Accuracy | Recall | Precision | Recall (any) | Multi-load | Misroute | False trigger "
                     "| +/− | n | Failed |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for arm_name, s in selection.items():
            lines.append(
                f"| `{arm_name}` | {fmt.percent(s.get('selection_accuracy'))} | {fmt.percent(s.get('recall'))} | "
                f"{fmt.percent(s.get('precision'))} | {fmt.percent(s.get('recall_any'))} | {fmt.percent(s.get('multi_load_rate'))} | "
                f"{fmt.percent(s.get('misroute_rate'))} | {fmt.percent(s.get('false_trigger_rate'))} | "
                f"{s.get('n_positive', 0)}/{s.get('n_negative', 0)} | {s.get('n', 0)} | "
                f"{s.get('n_failed', 0)} |"
            )
        lines.append("")

    evaluations = read(data, source="arms output", section="evaluations", required=False)
    if evaluations:
        lines.append("## Per-question scores")
        lines.append("")
        header = "| Question | " + " | ".join(f"`{a}`" for a in arms) + " |"
        sep = "| --- | " + " | ".join("---:" for _ in arms) + " |"
        lines.append(header)
        lines.append(sep)
        for ev in evaluations:
            q = ev.question_text
            q_short = (q[:60] + "…") if len(q) > 60 else q
            q_short = _md_cell(q_short)
            cells = [fmt.fixed(ev.score(arm), 0) for arm in arms]
            lines.append(f"| {q_short} | " + " | ".join(cells) + " |")
        lines.append("")

    return "\n".join(lines)
