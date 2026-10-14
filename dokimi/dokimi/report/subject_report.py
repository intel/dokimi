# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Markdown rendering for subject scorecards."""

from __future__ import annotations


def _fmt(value: object, digits: int = 3) -> str:
    """Render a rate, or ``n/a`` — a missing measurement is never printed as a zero."""
    return "n/a" if value is None else (f"{value:.{digits}f}" if isinstance(value, float) else str(value))


def _gate_status(gate: dict) -> str:
    """The cell's gate status, with how many of its checks this run did not measure."""
    skipped = gate.get("not_measured") or []
    status = gate.get("status", "n/a")
    return f"{status} ({len(skipped)} not measured)" if skipped else status


def _novelty_section(novelty: dict) -> list[str]:
    """The ``## Novelty`` section: one row per (artifact, model) cell, no subject-level total.

    Deliberately not sortable and deliberately not aggregated. Ranking artifacts by `added` inside
    one model correlated with the measured task delta at -0.446 over 13 artifacts, and in that same
    group the two validity checks flipped sign, so a column that invites a cross-artifact comparison
    would invite a wrong one. Each row is one pair, with its n and its soft-gate status.
    """
    lines = ["", "## Novelty"]
    cells = novelty.get("cells") or []
    if not cells:
        lines.append(
            "- not measured (attach `novelty analyze` artifacts with `--novelty-artifact`)"
        )
        return lines
    lines.extend([
        "",
        "| Artifact | Model | added (len/strict) | K0 (len/strict) | paired n | dead tok | Gate |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ])
    for cell in cells:
        paired = cell.get("paired", {}) or {}
        lenient = paired.get("lenient", {}) or {}
        strict = paired.get("strict", {}) or {}
        k0 = cell.get("k0", {}) or {}
        spans = cell.get("spans", {}) or {}
        dead = spans.get("frac_carrying_nothing")
        dead_s = _fmt(dead) + (" (capped)" if spans.get("capped_atom_set") else "")
        unread, tokens = spans.get("tokens_unread") or 0, spans.get("tokens") or 0
        if unread and tokens:
            # Over the read text only: a 0.000 on 2% of the text is not a clean artifact.
            share = unread / tokens
            dead_s += f" ({share:.0%} unread)" if share >= 0.01 else " (<1% unread)"
        lines.append(
            f"| `{(cell.get('artifact') or {}).get('ref', '?')}` | `{cell.get('model', '?')}` | "
            f"{_fmt(lenient.get('added'))} / {_fmt(strict.get('added'))} | "
            f"{_fmt(k0.get('lenient'))} / {_fmt(k0.get('strict'))} | "
            f"{lenient.get('n_paired', 'n/a')} | {dead_s} | "
            f"{_gate_status(cell.get('gate') or {})} |"
        )
    flagged = [
        (cell, check)
        for cell in cells
        for check in (cell.get("gate") or {}).get("checks", [])
        if not check.get("passed") and check.get("severity") == "warn"
    ]
    if flagged:
        lines.extend(["", "Soft-gate warnings (none of these block):"])
        for cell, check in flagged:
            ref = (cell.get("artifact") or {}).get("ref", "?")
            lines.append(f"- `{ref}` / `{cell.get('model', '?')}` — **{check['name']}**: "
                         f"{check['message']}")
    caveats = novelty.get("caveats") or []
    if caveats:
        lines.append("")
        lines.extend(f"- _{caveat}_" for caveat in caveats)
    return lines


def render_subject_scorecard(scorecard: dict) -> str:
    """Render a compact human-readable subject scorecard."""
    subject = scorecard.get("subject", {})
    suite = scorecard.get("suite", {})
    lines = [
        f"# Subject scorecard: {subject.get('id', 'unknown')}",
        "",
        f"- Kind: `{subject.get('kind')}`",
        f"- Ref: `{subject.get('ref')}`",
        f"- Baseline: `{scorecard.get('baseline')}`",
        f"- Products: {', '.join(suite.get('products', [])) or 'n/a'}",
        f"- Questions: {', '.join(suite.get('questions', [])) or 'n/a'}",
        "",
        "## Awareness runs",
    ]
    runs = scorecard.get("awareness", {}).get("runs", [])
    if runs:
        for run in runs:
            lines.append(
                f"- `{run.get('product')}` / `{run.get('questions')}` -> `{run.get('matrix_rollup')}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Cells"])
    cells = scorecard.get("cells", [])
    if cells:
        lines.append("| Product | Cell | Harness | Plugins | Artifact |")
        lines.append("| --- | --- | --- | --- | --- |")
        for cell in cells:
            lines.append(
                "| "
                f"{cell.get('product', '')} | "
                f"{cell.get('matrix_cell', '')} | "
                f"{cell.get('harness', '')} | "
                f"{cell.get('plugin_set', 'none')} | "
                f"`{cell.get('artifact', '')}` |"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Plugin deltas"])
    deltas = scorecard.get("plugin_deltas", [])
    if deltas:
        for row in deltas:
            lines.append(
                f"- `{row.get('baseline_cell')}` -> `{row.get('plugin_cell')}`: `{row.get('artifact')}`"
            )
    else:
        lines.append("- none")

    work = scorecard.get("work", {})
    lines.extend(["", "## Work runs"])
    lines.append(f"- Status: `{work.get('status', 'not_run')}`")
    tasks = work.get("tasks", [])
    lines.append(f"- Tasks: {', '.join(f'`{task}`' for task in tasks) if tasks else 'none'}")
    harnesses = work.get("harnesses", [])
    lines.append(f"- Harnesses: {', '.join(f'`{h}`' for h in harnesses) if harnesses else 'none'}")
    if work.get("task_runs"):
        lines.append(f"- Artifact: `{work.get('task_runs')}`")
    if work.get("task_report"):
        lines.append(f"- Report: `{work.get('task_report')}`")
    per_harness = work.get("summary", {}).get("per_harness", {})
    if per_harness:
        lines.append("")
        lines.append("| Harness | Pass rate | Passed | Scored | Errored |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for harness, stats in per_harness.items():
            rate = stats.get("pass_rate")
            rate_s = "n/a" if rate is None else f"{rate:.2%}"
            lines.append(
                f"| `{harness}` | {rate_s} | {stats.get('passed', 0)} | "
                f"{stats.get('scored', stats.get('n', 0))} | {stats.get('errored', 0)} |"
            )

    lines.extend(_novelty_section(scorecard.get("novelty", {}) or {}))

    warnings = scorecard.get("warnings", [])
    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in warnings)

    return "\n".join(lines) + "\n"
