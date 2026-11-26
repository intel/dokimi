# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Limitation notes of ``report model-compare`` for runs without a treatment delta (#965)."""

from __future__ import annotations

BASELINE_ONLY_LIMITATION = [
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
    "> dokimi arms run \\",
    ">   --product oneTBB --questions questions/oneTBB.json \\",
    ">   --arm \"baseline,docs\" \\",
    ">   --judge \\",
    ">   --judge-model claude-sonnet-5-5",
    "> ```",
    "> ",
    "> Treatment arm options: `docs` (Context7 documentation), `skill:<path>` (local skill), `agent` (agentic doc retrieval).",
    "> ",
    "> See: `docs/benchmarking-and-comparison.md#treatment-arm-comparison`",
    "",
]


def no_delta_limitation(runs: list[tuple[str, dict]], arms: set[str], paired_in_a_run: bool, pooled: bool = False) -> list[str]:
    """The limitation for runs with no common question scored in both arms (#965).

    Names the arms the runs contain (*arms*, the non-baseline ones); with no treatment arm at all it is the baseline-only text.
    ``paired_in_a_run`` is true when some run has a question scored in both arms before pooling or
    the common-question cut, so the missing pair is shared ones, not pairs as such. ``pooled`` is true
    when replicates were pooled; the text mentions pooling only then.
    """
    if not arms:
        return BASELINE_ONLY_LIMITATION
    baselines = sorted({b for _, data in runs if isinstance(b := data.get("baseline_arm"), str) and b})
    named = ", ".join(f"`{a}`" for a in [*baselines, *sorted(arms)])
    if paired_in_a_run:
        cut = "once replicates are pooled and only the questions every run scored are kept" if pooled else "once only the questions every run scored are kept"
        return [
            f"> **⚠️ Report Limitation:** These runs contain the arms {named}, but no question scored in both arms is left {cut}.",
            "> ",
            "> Delta, significance, head-to-head and benefit analysis are unavailable until a question that every run scored has a score in both arms.",
            "",
        ]
    return [
        f"> **⚠️ Report Limitation:** These runs contain the arms {named}, but no question has scores in both arms.",
        "> ",
        "> Delta, significance, head-to-head and benefit analysis are unavailable until a question is scored in both arms.",
        "",
    ]
