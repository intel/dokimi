# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Markdown report renderers."""

from __future__ import annotations

from pathlib import Path


def _summary_lines(summary: dict) -> list[str]:
    """Render run summary lines in a consistent fixed-precision format."""
    return [
        f"- docs: {summary['docs']}",
        f"- score: {summary['score']:.4f}",
        f"- coverage: {summary['coverage']:.4f}",
        f"- freshness_lite: {summary['freshness_lite']:.4f}",
        f"- readability: {summary['readability']:.4f}",
    ]


def write_run_report(run_data: dict, path: Path) -> None:
    """Write markdown report for a benchmark run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Dokimi Run", "", "## Summary", *(_summary_lines(run_data["summary"]))]
    
    # Add gate status if present
    if "gate" in run_data and run_data["gate"].get("soft", {}).get("enabled"):
        gate = run_data["gate"]["soft"]
        status_emoji = "✅" if gate["passed"] else "❌"
        lines.extend([
            "",
            "## Soft Gate",
            f"{status_emoji} **Status:** {('PASS' if gate['passed'] else 'FAIL')}",
            f"- Min score: {gate['min_score']:.4f}",
            f"- Actual score: {run_data['summary']['score']:.4f}",
        ])
    
    lines.extend(["", "## Docs"])
    for d in run_data["docs"]:
        lines.append(
            f"- {d['path']}: score={d['score']:.4f} cov={d['coverage']:.4f} "
            f"fresh={d['freshness_lite']:.4f} read={d['readability']:.4f} chunks={d['chunks']}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_compare_report(compare_data: dict, path: Path) -> None:
    """Write markdown report for snapshot comparison."""
    path.parent.mkdir(parents=True, exist_ok=True)
    d = compare_data["diff"]
    lines = [
        "# Dokimi Compare",
        "",
        "## Diff",
        f"- docs: {d['docs']:+d}",
        f"- score: {d['score']:+.4f}",
        f"- coverage: {d['coverage']:+.4f}",
        f"- freshness_lite: {d['freshness_lite']:+.4f}",
        f"- readability: {d['readability']:+.4f}",
    ]
    
    # Add regression analysis if present
    if "regressions" in compare_data:
        reg = compare_data["regressions"]
        lines.extend(["", "## Regression Analysis"])
        
        # Overall status. A snapshot with no doc scored nothing: a zero diff against it is not "no regressions".
        unscored = [side for side in ("base", "candidate")
                    if side in compare_data and not compare_data[side].get("docs")]
        nothing = f"⚪ **Nothing to compare (0 scored)**: no document in {' and '.join(unscored)}"
        if reg["has_critical"]:
            lines.append("🔴 **CRITICAL regressions detected**")
        elif reg["has_warnings"]:
            lines.append("🟡 **Warnings detected**")
        elif unscored:
            lines.append(nothing)
        else:
            lines.append("✅ **No regressions**")
        if unscored and (reg["has_critical"] or reg["has_warnings"]):
            lines.append(nothing)
        
        lines.append("")
        
        # Score regression
        score_r = reg["score"]
        severity_emoji = {"OK": "✅", "WARN": "🟡", "CRITICAL": "🔴"}
        lines.append(f"{severity_emoji[score_r['severity']]} **Score:** {score_r['delta']:+.4f} ({score_r['severity']})")
        
        # Metric regressions
        for m in reg["metrics"]:
            lines.append(f"{severity_emoji[m['severity']]} **{m['metric']}:** {m['delta']:+.4f} ({m['severity']})")
    
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
