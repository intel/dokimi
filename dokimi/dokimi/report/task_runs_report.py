# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Render an executable-task run (``task_runs.v1``) as Markdown.

Mirrors :mod:`dokimi.report.arms_report` for the Q&A track: takes the
artifact produced by :class:`~dokimi.harnesses.runner.TaskSuiteRunner`
and renders a headline, a difficulty rollup, the per-harness comparison, a
per-task pass/cost table, and a per-cell answer detail.

Aggregates are keyed by the **full harness id**, so a run with several harnesses
(``--harnesses codex,claude-code`` or two skill harnesses) keeps them distinct
rather than collapsing them into one bucket.

The per-cell detail contrasts the verifier verdict with the model's own
self-report (``solver.json.result`` in each cell's ``output_dir``); cells where
the model claimed success yet the verifier scored 0 are the most informative.
Reading ``solver.json`` is best-effort — the report still renders if the cell
directories are gone (the self-report column is just blank).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from dokimi.eval.binary_stats import PASS_RATE_CI_CONFIDENCE
from dokimi.harnesses.runner import is_errored


def _or_dash(value: object) -> object:
    return "—" if value is None else value


def _md_cell(text: str) -> str:
    """Make text safe for a single Markdown table cell."""
    return (text or "").replace("\n", " ").replace("|", r"\|")


def _fmt_cost(v: float | None) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    # A real but sub-cent per-pass cost is not $0.0000.
    return "<$0.0001" if 0 < v < 0.00005 else f"${v:.4f}"


def _fmt_norm(v: Any, reason: str | None) -> str:
    """A normalized gain/change cell; a null says why when the reason is known."""
    if isinstance(v, (int, float)):
        return f"{v:+.2f}"
    return f"— ({reason})" if reason else "—"


def _self_report(output_dir: str | None) -> tuple[str, int | None]:
    """Best-effort (one-line model self-report, num_turns) from solver.json."""
    if not output_dir:
        return "", None
    solver = Path(output_dir) / "solver.json"
    if not solver.is_file():
        return "", None
    try:
        obj = json.loads(solver.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return "", None
    text = (obj.get("result") or "").strip().replace("\n", " ")
    return text[:160], obj.get("num_turns")


def _fmt_p(p: Any) -> str:
    """Render a p-value; ``—`` means no test could run, which is not ``p = 1``."""
    if not isinstance(p, (int, float)):
        return "—"
    return "<0.0001" if p < 0.0001 else f"{p:.4f}"


def _mcnemar_lines(cmp: dict[str, Any], baseline: str | None) -> list[str]:
    """Paired significance table for the pass/fail outcomes.

    Pass rates on this track are binary per task, so the comparison is McNemar's
    exact test on the disagreements rather than a t-test on scores.
    """
    rows = [(h, cr["mcnemar"]) for h, cr in cmp.items() if cr.get("mcnemar")]
    if not rows:
        return []

    lines = [
        "## Paired significance vs baseline (McNemar)",
        "",
        f"_Each harness is paired per task-repeat against `{baseline}`. Only the "
        "**discordant** cells — tasks one arm solved and the other did not — "
        "carry evidence; tasks both arms passed or both failed are shown so a "
        "null result can be told apart from a suite with no headroom left. "
        f"p is the two-sided exact binomial, adjusted across the {len(rows)} "
        "harness(es) compared here (Holm-Bonferroni)._",
        "",
        "| Harness | Pass-rate Δ | 95% CI | Fixed | Broke | Both pass | Both fail "
        "| p | p (adj) | Sig. | n |",
        "| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: | ---: |",
    ]
    for harness, mc in rows:
        significant = mc.get("significant_adjusted")
        if significant is None:
            significant = mc.get("significant")
        sig_s = "—" if significant is None else ("yes" if significant else "no")
        lo, hi = mc.get("ci_lo"), mc.get("ci_hi")
        ci_s = (
            f"[{lo*100:+.1f}, {hi*100:+.1f}] pp"
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float))
            else "—"
        )
        lines.append(
            f"| `{harness}` | {mc.get('paired_pass_rate_delta', 0)*100:+.2f} pp | {ci_s} | "
            f"{mc.get('n_gained', 0)} | {mc.get('n_lost', 0)} | "
            f"{mc.get('n_both_passed', 0)} | {mc.get('n_both_failed', 0)} | "
            f"{_fmt_p(mc.get('p_exact'))} | {_fmt_p(mc.get('p_adjusted'))} | "
            f"{sig_s} | {mc.get('n', 0)} |"
        )
    excluded = sum(mc.get("n_excluded", 0) for _, mc in rows)
    if excluded:
        lines += [
            "",
            f"_{excluded} cell-exclusion(s) summed across the comparisons above: "
            "cells present for one harness only, or where a harness itself "
            "errored (missing data, not a verified failure)._",
        ]
    if any(mc.get("pseudo_replicated") for _, mc in rows):
        units = min(mc.get("n_units", 0) for _, mc in rows)
        lines += [
            "",
            f"_⚠️ Repeats are paired as separate cells, so the {rows[0][1].get('n', 0)} "
            f"pairs come from only {units} independent task(s). Repeats of one task "
            "share its difficulty, so the exact p above is anti-conservative — read "
            "it as a screening signal, not a claim at α = 0.05._",
        ]
    lines.append("")
    return lines


def _fmt_rate(v: Any) -> str:
    return f"{v*100:.0f}%" if isinstance(v, (int, float)) and not isinstance(v, bool) else "—"


def _discovery_lines(data: dict[str, Any]) -> list[str]:
    """The ``-skill-discover`` arms beside their with-skill pair (``summary.discovery_gaps``)."""
    gaps = data.get("summary", {}).get("discovery_gaps") or {}
    if not gaps:
        return []
    lines = [
        "## Skill discovery",
        "",
        "_The discovery arm installs the same skill as its with-skill arm but does "
        "not announce it, so the agent finds it by its description or not at all. "
        "Gap = with-skill pass rate − discovery pass rate; skill invoked = rows "
        "that loaded the skill with the `Skill` tool._",
        "",
        "| Discovery arm | With-skill pass | Discovery pass | Gap | "
        "With-skill invoked | Discovery invoked |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for h, g in gaps.items():
        gap = g.get("gap")
        if isinstance(gap, (int, float)):
            gap_s = f"{gap*100:+.2f} pp"
        elif g.get("with_skill_pass_rate") is None or g.get("discover_pass_rate") is None:
            gap_s = "— (no scored cells)"
        else:
            # Artifacts from before the two flags were recorded get the combined reading.
            ran, lost = g.get("attempted_cells_match"), g.get("errored_cells_match")
            if ran is False and lost is False:
                gap_s = "— (arms ran and lost different cells)"
            elif ran is False:
                gap_s = "— (arms ran different cells)"
            elif lost is False:
                gap_s = "— (arms lost different cells)"
            else:
                gap_s = "— (arms ran or lost different cells)"
        lines.append(f"| `{h}` | {_fmt_rate(g.get('with_skill_pass_rate'))} | "
                     f"{_fmt_rate(g.get('discover_pass_rate'))} | {gap_s} | "
                     f"{_fmt_rate(g.get('with_skill_invocation_rate'))} | "
                     f"{_fmt_rate(g.get('discover_invocation_rate'))} |")
    cut = sum(1 for r in data.get("results", []) if r.get("harness") in gaps
              and (r.get("metrics", {}).get("skill_listing") or {}).get("truncated"))
    if cut:
        lines += ["", f"_⚠️ {cut} discovery row(s) ran with a description longer than the "
                  "skill listing shows; the agent saw only its start._"]
    lines.append("")
    return lines


def render_task_runs_report(data: dict[str, Any]) -> str:
    """Render a ``task_runs.v1`` artifact as Markdown."""
    lines: list[str] = []
    ph = data.get("summary", {}).get("per_harness", {})
    harnesses = list(ph.keys())
    diff_of = {
        t["name"]: t.get("metadata", {}).get("difficulty", "?")
        for t in data.get("tasks", [])
    }
    rows = data.get("results", [])
    baseline = data.get("baseline_harness")
    # Fall back to harnesses observed in the rows if the summary is empty.
    if not harnesses:
        seen: list[str] = []
        for r in rows:
            if r["harness"] not in seen:
                seen.append(r["harness"])
        harnesses = seen

    lines.append("# Executable task-run report")
    lines.append("")
    lines.append(f"- Model: `{data.get('model', '?')}`")
    lines.append(f"- Repeats: {data.get('repeats', 1)}")
    lines.append(f"- Harnesses: {', '.join(f'`{h}`' for h in harnesses)}")
    lines.append(f"- Baseline: `{baseline}`" if baseline else "- Baseline: —")
    lines.append(f"- Generated: `{data.get('generated_at', '?')}`")
    lines.append("")

    # ── headline ──
    lines.append("## Headline")
    lines.append("")
    lines.append(f"| Harness | Pass rate | Errored | {PASS_RATE_CI_CONFIDENCE:.0%} CI (Wilson) | "
                 "Cost (USD) | $/pass | Wall (min) | "
                 "Completion tok | Cache read tok | Cost-known | Skill invoked (rows reporting) |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for h in harnesses:
        s = ph.get(h, {})
        c = s.get("cost", {})
        rate = s.get("pass_rate")
        scored = s.get("scored", s.get("n", 0))
        rate_s = "—" if rate is None else f"{rate*100:.2f}% ({s.get('passed', 0)}/{scored})"
        ci = s.get("pass_rate_ci") or {}
        ci_s = (f"{ci['lo']*100:.0f}–{ci['hi']*100:.0f}%"
                if isinstance(ci.get("lo"), (int, float)) and isinstance(ci.get("hi"), (int, float))
                else "—")
        sir = s.get("skill_invocation_rate")
        sir_s = (f"{sir*100:.0f}% ({s.get('skill_invocation_n', 0)})"
                 if isinstance(sir, (int, float)) else "—")
        lines.append(
            f"| `{h}` | {rate_s} | {s.get('errored', 0)} | {ci_s} | {_fmt_cost(c.get('total_cost_usd'))} | "
            f"{_fmt_cost(c.get('cost_per_pass_usd'))} | "
            f"{s.get('elapsed_sec', 0)/60:.1f} | {_or_dash(c.get('completion_tokens'))} | "
            f"{_or_dash(c.get('cache_read_tokens'))} | "
            f"{c.get('cost_known_n', '—')}/{s.get('n', 0)} | {sir_s} |"
        )
    lines.append("")
    if any(ph.get(h, {}).get("errored") for h in harnesses):
        lines.append("_Pass rate is over scored cells. An errored cell is one whose "
                     "harness raised (docker build, exec, config) or the solver CLI made "
                     "no attempt (rejected credentials): it produced no verdict, so it is "
                     "missing data, not a 0. An agent or verifier timeout is a failure, "
                     "not an error._")
        lines.append("")

    # ── per-cell aggregation, keyed by full harness id ──
    # cost is None until a row actually reports cost_usd, so harnesses without
    # LLM telemetry (oracle, dry runs) render "—" rather than a fake $0.0000.
    tasks = sorted({r["task"] for r in rows})
    cell: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"passed": 0, "n": 0, "errored": 0, "cost": None, "detail": []}
    )
    for r in rows:
        key = (r["task"], r["harness"])
        c = cell[key]
        errored = is_errored(r)
        c["errored"] += 1 if errored else 0
        c["n"] += 0 if errored else 1
        c["passed"] += 1 if r.get("passed") and not errored else 0
        cost = r.get("metrics", {}).get("cost_usd")
        if isinstance(cost, (int, float)):
            c["cost"] = (c["cost"] or 0.0) + cost
        claim, turns = _self_report(r.get("output_dir"))
        c["detail"].append({
            "repeat": r.get("repeat", 1), "reward": r.get("reward"),
            "passed": bool(r.get("passed")) and not errored, "errored": errored,
            "rc": r.get("returncode"),
            "cost": cost, "turns": turns, "claim": claim,
        })

    # ── difficulty rollup (one column pair per harness) ──
    lines.append("## Difficulty rollup")
    lines.append("")
    lines.append("| Difficulty | Tasks | " + " | ".join(f"`{h}` pass" for h in harnesses) + " |")
    lines.append("|-----------|------:|" + "|".join("------:" for _ in harnesses) + "|")
    by_diff: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: {h: [0, 0, 0] for h in harnesses}
    )
    for t in tasks:
        dlv = diff_of.get(t, "?")
        for h in harnesses:
            by_diff[dlv][h][0] += cell[(t, h)]["passed"]
            by_diff[dlv][h][1] += cell[(t, h)]["n"]
            by_diff[dlv][h][2] += cell[(t, h)]["errored"]
    for dlv in sorted(by_diff):
        n_tasks = sum(1 for t in tasks if diff_of.get(t) == dlv)
        cols = []
        for h in harnesses:
            p, n, e = by_diff[dlv][h]
            cols.append((f"{p}/{n}" if n else "—") + (f" (+{e} err)" if e else ""))
        lines.append(f"| {dlv} | {n_tasks} | " + " | ".join(cols) + " |")
    lines.append("")

    # ── comparison (straight from artifact) ──
    cmp = data.get("summary", {}).get("comparisons", {})
    if cmp:
        lines.append("## Comparison vs baseline")
        lines.append("")
        lines.append("| Harness | Pass-rate Δ | Normalized gain (Hake) | Normalized change | "
                     "Passed Δ | Op-count Δ | Cost Δ (USD) |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for h, cr in cmp.items():
            prd = cr.get("pass_rate_delta")
            withheld = cr.get("passed_delta", 0) is None
            if isinstance(prd, (int, float)):
                prd_s = f"{prd*100:+.2f} pp"
            elif withheld:
                prd_s = "— (arms lost different cells)"
            else:
                prd_s = "—"
            # With a delta in hand, a null gain/change is the formula's own
            # undefined case; without one, it is withheld with the delta. An
            # artifact older than these keys has no reason to give.
            has_delta = isinstance(prd, (int, float))
            ng_s = _fmt_norm(cr.get("normalized_gain"), "withheld" if withheld else
                             "baseline at 100%" if has_delta and "normalized_gain" in cr
                             else None)
            nc_s = _fmt_norm(cr.get("normalized_change"), "withheld" if withheld else
                             "both at 0% or 100%" if has_delta and "normalized_change" in cr
                             else None)
            pd = cr.get("passed_delta", 0)
            pd_s = "—" if pd is None else f"{pd:+}"
            cd = cr.get("cost_delta_usd")
            cd_s = f"{cd:+.4f}" if isinstance(cd, (int, float)) else "—"
            lines.append(f"| `{h}` | {prd_s} | {ng_s} | {nc_s} | {pd_s} | "
                         f"{cr.get('operation_count_delta', 0):+} | {cd_s} |")
        lines.append("")
        lines.extend(_mcnemar_lines(cmp, baseline))
    lines.extend(_discovery_lines(data))

    # ── per-task pass + cost (one column pair per harness) ──
    lines.append("## Per-task pass + cost")
    lines.append("")
    lines.append("| Task | Diff | " + " | ".join(
        f"`{h}` pass | `{h}` $" for h in harnesses
    ) + " |")
    lines.append("| --- | :---: |" + "|".join(" :---: | ---: " for _ in harnesses) + "|")
    for t in tasks:
        cols = []
        for h in harnesses:
            c = cell[(t, h)]
            err_s = f" (+{c['errored']} err)" if c["errored"] else ""
            pass_s = f"{c['passed']}/{c['n']}" if c["n"] else "—"
            cols.append(f"{pass_s}{err_s} | {_fmt_cost(c['cost'])}")
        lines.append(f"| {t} | {diff_of.get(t, '?')} | " + " | ".join(cols) + " |")
    lines.append("")

    # ── per-cell answer detail ──
    lines.append("## Per-cell answer detail")
    lines.append("")
    lines.append("Each repeat: verifier reward, pass/fail, turns, cost, and the model's "
                 "own one-line self-report. ⚠️ marks cells where the model claimed "
                 "success (\"done\") yet the verifier scored 0 — usually correct code "
                 "that missed the performance bar the verifier enforces.")
    lines.append("")
    for t in tasks:
        lines.append(f"### {t} ({diff_of.get(t, '?')})")
        lines.append("")
        lines.append("| Harness | Rep | Reward | Pass | Turns | Cost | Model self-report |")
        lines.append("| --- | :---: | :---: | :---: | :---: | ---: | --- |")
        for h in harnesses:
            for det in sorted(cell[(t, h)]["detail"], key=lambda x: x["repeat"]):
                rw = det["reward"]
                rw_s = f"{rw:g}" if isinstance(rw, (int, float)) else "null"
                claimed = det["claim"] and "done" in det["claim"].lower()
                if det["errored"]:
                    pass_s = "ERR"
                else:
                    pass_s = "✅" if det["passed"] else ("❌ ⚠️" if claimed else "❌")
                turns_s = det["turns"] if det["turns"] is not None else "—"
                claim = det["claim"] or (
                    "(timeout rc124)" if det["rc"] == 124
                    else "(harness error)" if det["errored"] else "(no result)"
                )
                lines.append(f"| `{h}` | {det['repeat']} | {rw_s} | {pass_s} | {turns_s} | "
                             f"{_fmt_cost(det['cost'])} | {_md_cell(claim)} |")
        lines.append("")

    return "\n".join(lines)
