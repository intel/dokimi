# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Render DashboardData as Markdown."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from dokimi.dashboard.aggregator import DashboardData, ProductSnapshot


def _score_bar(score: Optional[float], width: int = 10) -> str:
    if score is None:
        return "·" * width
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _status_emoji(status: str) -> str:
    return {"good": "🟢", "fair": "🟡", "poor": "🔴", "no-data": "⚪"}.get(status, "⚪")


def _md_cell(text: str) -> str:
    """Escape pipe and newlines so text is safe inside a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "").strip()


def _fmt_delta(delta: Optional[float]) -> str:
    if delta is None:
        return "—"
    return f"+{delta:.1f}" if delta >= 0 else f"{delta:.1f}"


def render_dashboard(data: DashboardData, top_n_bad_questions: int = 5) -> str:
    lines = []
    lines.append("# Dokimi Dashboard")
    lines.append(f"\n_Generated: {data.generated_at}_\n")

    if not data.products:
        lines.append("_No evaluation results found. Run `benchmark batch --all` to generate data._")
        return "\n".join(lines)

    # ── Summary table ──────────────────────────────────────────────────────
    lines.append("## Overview\n")
    lines.append("| Status | Product | Context Arm | Baseline | Delta | Questions | Evaluated |")
    lines.append("|--------|---------|-----------|--------------|-------|-----------|-----------|")

    for p in data.sorted_by_score:
        emoji = _status_emoji(p.status)
        with_s = f"{p.avg_with_docs:.1f}" if p.avg_with_docs is not None else "—"
        without_s = f"{p.avg_without_docs:.1f}" if p.avg_without_docs is not None else "—"
        delta_s = _fmt_delta(p.avg_delta)
        date = p.evaluated_at[:10] if p.evaluated_at else "—"
        lines.append(
            f"| {emoji} | **{_md_cell(p.product)}** | {with_s} | {without_s} | {delta_s} | {p.total_questions} | {date} |"
        )

    # ── Score distribution bar ─────────────────────────────────────────────
    lines.append("\n## Score Distribution\n")
    lines.append("```")
    for p in data.sorted_by_score:
        score = p.doc_score
        bar = _score_bar(score)
        score_str = f"{score:5.1f}" if score is not None else "  n/a"
        lines.append(f"{p.product:<30} {bar} {score_str}")
    lines.append("```")

    # ── Per-product drill-down ─────────────────────────────────────────────
    lines.append("\n## Per-Product Details\n")
    for p in data.sorted_by_score:
        lines.extend(_render_product_section(p, top_n_bad_questions))

    return "\n".join(lines)


def _render_product_section(p: ProductSnapshot, top_n: int) -> list[str]:
    lines = []
    emoji = _status_emoji(p.status)
    lines.append(f"### {emoji} {p.product}\n")

    with_s = f"{p.avg_with_docs:.1f}/100" if p.avg_with_docs is not None else "—"
    without_s = f"{p.avg_without_docs:.1f}/100" if p.avg_without_docs is not None else "—"
    delta_s = _fmt_delta(p.avg_delta)

    lines.append(f"- **Context-arm score**: {with_s}")
    lines.append(f"- **Baseline score**: {without_s}")
    lines.append(f"- **Delta (context benefit)**: {delta_s}")
    lines.append(f"- **Questions evaluated**: {p.total_questions}")
    lines.append(f"- **Judge model**: {p.judge_model}")
    lines.append(f"- **Evaluated**: {p.evaluated_at[:19] if p.evaluated_at else '—'}")

    # By-source breakdown (if mixed persona + chunk questions)
    if p.by_source and len(p.by_source) > 1:
        lines.append("\n**Scores by question source:**\n")
        lines.append("| Source | Count | Context Arm | Baseline | Delta | Grounded |")
        lines.append("|--------|------:|----------:|-------------:|------:|---------:|")
        for source, stats in sorted(p.by_source.items()):
            w = f"{stats['avg_with_docs']:.1f}" if stats["avg_with_docs"] is not None else "—"
            wo = f"{stats['avg_without_docs']:.1f}" if stats["avg_without_docs"] is not None else "—"
            d = _fmt_delta(stats["avg_delta"])
            grounded = f"{stats['grounded']}/{stats['count']}" if stats.get("grounded") is not None else "—"
            lines.append(f"| `{source}` | {stats['count']} | {w} | {wo} | {d} | {grounded} |")

    # Worst questions
    bad = [q for q in p.questions if q.with_docs_score is not None][:top_n]
    if bad:
        lines.append(f"\n**Bottom {len(bad)} questions (needs improvement):**\n")
        lines.append("| Score | Δ | Src | Question |")
        lines.append("|-------|---|-----|---------|")
        for q in bad:
            score = f"{q.with_docs_score:.0f}" if q.with_docs_score is not None else "—"
            delta = _fmt_delta(round(q.delta, 0) if q.delta is not None else None).replace(".0", "")
            src = "🧩" if q.question_source == "chunk" else "👤"
            question_short = _md_cell(q.question[:78] + ("…" if len(q.question) > 78 else ""))
            lines.append(f"| {score} | {delta} | {src} | {question_short} |")

    lines.append("")
    return lines


def save_dashboard_markdown(data: DashboardData, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard(data), encoding="utf-8")


def save_dashboard_json(data: DashboardData, path: Path) -> None:
    """Save ``data`` as a ``dashboard`` artifact (``ArtifactValidationError``: kept at ``<path>.rejected``)."""
    from dataclasses import asdict

    from dokimi.artifacts import save_artifact

    save_artifact("dashboard", asdict(data), path)
