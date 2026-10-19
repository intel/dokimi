#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""
generate_report.py — Generate a documentation quality report from eval JSON.

Usage:
    python generate_report.py --eval results/onedal_final/eval/oneDAL.json \
                              --out results/onedal_final/reports/oneDAL_full.md

    python generate_report.py --eval results/onetbb_final/eval/oneTBB.json \
                              --out results/onetbb_final/reports/oneTBB_full.md
"""

import argparse
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

from dokimi.eval import stats as eval_stats
from dokimi.records import BASELINE, CONTEXT, Record, RecordError, record, records

#: Mirrors :data:`dokimi.eval.stats.HAS_SCIPY`; the tests themselves
#: live there now. Note this module defines a local ``stats()`` helper, hence
#: the ``eval_stats`` alias.
HAS_SCIPY = eval_stats.HAS_SCIPY

try:
    from zoneinfo import ZoneInfo
    _TZ: ZoneInfo | None = ZoneInfo("America/Los_Angeles")
except ImportError:
    _TZ = None


def avg(lst):
    return round(sum(lst) / len(lst), 1) if lst else 0.0


def significance_test(evals):
    """Paired t-test, Wilcoxon, and Cohen's d_z on (with_docs - without_docs) pairs.

    Delegates to :func:`dokimi.eval.stats.paired_test`, which is also
    what the arms and model-comparison reports use, so the three cannot drift
    apart. Two behaviour changes came with that:

    * ``delta_std``/``cohens_d`` now use the sample SD (n-1). This copy divided
      by ``n``, which inflated d_z relative to every other report in the repo.
    * ``significant`` also requires Wilcoxon < 0.05 when it could be computed,
      matching the other reports instead of trusting the t-test alone.

    ``significant`` stays ``None`` when no test could run (no SciPy, too few
    pairs, or zero-variance deltas) rather than claiming "not significant".
    """
    pairs = []
    for e in records(evals, kind="evaluations"):
        w, wo = e.score(CONTEXT), e.score(BASELINE)
        if w is not None and wo is not None:
            pairs.append((w, wo))
    if len(pairs) < eval_stats.MIN_PAIRS_FOR_TEST:
        return None

    result = eval_stats.paired_test([p[0] for p in pairs], [p[1] for p in pairs])
    if result is None:
        return None

    # No multiplicity correction on this path; see eval/stats.arm_significance.
    result.pop("p_adjusted", None)
    result.pop("significant_adjusted", None)
    if result["p_ttest"] is None:
        result["significant"] = None
    return result


def has_scores(e: Record) -> bool:
    return e.score(CONTEXT) is not None and e.score(BASELINE) is not None


def _score_cell(e: Record, condition: str):
    """The condition's aggregate as written, or ``N/A`` when it has none."""
    score = e.score(condition)
    return "N/A" if score is None else score


def _difficulty(e: Record, default):
    # Absent and null differ here, as they always have: a record without the key
    # prints ``default``, one with ``difficulty: null`` prints None.
    return e.raw.get("difficulty", default)


def stats(qs):
    valid = [e for e in qs if has_scores(e)]
    with_s = [e.score(CONTEXT) for e in valid]
    without_s = [e.score(BASELINE) for e in valid]
    deltas = [d for e in valid if (d := e.delta) is not None]
    return {
        "count": len(qs),
        "valid": len(valid),
        "with_avg": avg(with_s),
        "without_avg": avg(without_s),
        "delta_avg": round(avg(with_s) - avg(without_s), 1),
        "improvements": sum(1 for d in deltas if d > 0),
        "degradations": sum(1 for d in deltas if d < 0),
        "neutral": sum(1 for d in deltas if d == 0),
    }


def fmt_delta(d):
    if d is None:
        return "N/A"
    if isinstance(d, float):
        d = round(d, 1)
    return f"+{d}" if d > 0 else str(d)


def fmt_question(text: str | None) -> str:
    """Keep full question text, but make it markdown-table safe."""
    if text is None:
        return ""
    return str(text).replace("\n", " ").replace("|", "\\|").strip()


def detect_static_prefix(evals):
    """Detect the prefix used for static/golden questions (e.g. 'onedal-Q', 'onetbb-Q')."""
    for e in evals:
        qid = e.question_id
        # Static questions typically look like 'onedal-Q001', 'onetbb-Q001', etc.
        import re
        m = re.match(r"^([a-zA-Z]+-Q)\d+$", str(qid))
        if m:
            return m.group(1)
    return None


DIAG_MAP = {
    "docs_helped": "✅ Docs helped",
    "knowledge_sufficient": "🔵 Model knowledge sufficient",
    "empty_retrieval": "🔴 Empty retrieval",
    "low_relevance": "🟡 Low relevance",
    "below_rerank_threshold": "🟠 Below rerank threshold",
    "insufficient_data": "⚪ Insufficient data",
}

DIFFICULTY_ORDER = ["easy", "beginner", "intermediate", "medium", "advanced", "hard"]


def _answers_by_id(answers_path: str) -> dict[str, Record]:
    """The answers file next to an eval file, by question key, for the Q&A sidecar.

    A missing answers file means no answers in the sidecar. A record that cannot be
    read drops only itself, with a warning naming it.
    """
    import sys
    from pathlib import Path

    try:
        data = json.loads(Path(answers_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    items = data.get("answers") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {}
    out: dict[str, Record] = {}
    for i, item in enumerate(items):
        try:
            rec = record(item, source=f"{answers_path} answers", index=i, kind="answers")
        except RecordError as exc:
            print(f"⚠ {exc}; its answers are left out of the Q&A sidecar", file=sys.stderr)
            continue
        out.setdefault(rec.key, rec)
    return out


def generate_report(eval_path: str, out_path: str, qa_json_out: str | None = None):
    with open(eval_path, encoding="utf-8") as f:
        data = json.load(f)

    evals = records(data["evaluations"], source=eval_path, kind="evaluations")
    meta = data.get("run_metadata", {})
    library = os.path.basename(eval_path).replace(".json", "")

    static_prefix = detect_static_prefix(evals)
    static_qs = [e for e in evals if static_prefix and e.key.startswith(static_prefix)]
    dynamic_qs = [e for e in evals if not (static_prefix and e.key.startswith(static_prefix))]

    all_stats = stats(evals)
    static_stats = stats(static_qs)
    dyn_stats = stats(dynamic_qs)

    diff_groups = defaultdict(list)
    for e in evals:
        diff_groups[_difficulty(e, "unknown")].append(e)

    valid_all = [
        e for e in evals
        if has_scores(e) and e.delta is not None and not math.isnan(e.delta)
    ]

    lines = []

    # Header + context
    lines += [
        f"# {library} Documentation Quality Report",
        f"_Generated: {datetime.now(tz=_TZ).strftime('%Y-%m-%d %H:%M %Z') if _TZ else datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        "## Run Configuration",
        "| | |",
        "|---|---|",
        f'| Answer model | {meta.get("answer_model", "—")} ({meta.get("answer_provider", "—")}) |',
        f'| Judge model | {data.get("judge_model", "—")} ({data.get("judge_provider", "—")}) |',
        f'| Total questions | {all_stats["count"]} ({dyn_stats["count"]} dynamic + {static_stats["count"]} golden static) |',
        "",
    ]

    # Legend
    lines += [
        "## How to Read This Report",
        "",
        "Each answer is scored **0–100** by the judge model across 5 dimensions: "
        "correctness, completeness, specificity, code quality, actionability. "
        "**Aggregate** is their weighted average.",
        "",
        "**Delta = context-arm score − baseline score**",
        "Positive = context helped. Zero/negative = model already knew the answer, or retrieved chunks were off-topic.",
        "",
        "| Question type | Description |",
        "|---|---|",
        "| 🔵 static (golden) | Hand-curated questions with known expected answers — highest signal |",
        "| 🟡 dynamic (generated) | Auto-generated from library docs and user personas |",
        "",
        "| Diagnosis | Meaning |",
        "|---|---|",
        "| ✅ Context helped | Delta > 0 — retrieved context improved the answer |",
        "| 🔵 Baseline sufficient | Delta ≤ 0 — model answered without extra context |",
        "| 🔴 Empty retrieval | No chunks retrieved |",
        "| 🟡 Low relevance | Retrieved chunks were off-topic |",
        "| ⚪ Insufficient data | Score or answer missing |",
        "",
    ]

    # Summary
    lines += [
        "## Summary",
        "| Set | Count | Context arm | Baseline | Delta |",
        "|---|---:|---:|---:|---:|",
        f'| **All** | {all_stats["count"]} | {all_stats["with_avg"]} | {all_stats["without_avg"]} | **{all_stats["delta_avg"]:+}** |',
        f'| Generated (dynamic) | {dyn_stats["count"]} | {dyn_stats["with_avg"]} | {dyn_stats["without_avg"]} | **{dyn_stats["delta_avg"]:+}** |',
        f'| Golden (static) | {static_stats["count"]} | {static_stats["with_avg"]} | {static_stats["without_avg"]} | **{static_stats["delta_avg"]:+}** |',
        "",
    ]

    # Statistical significance
    sig = significance_test(evals)
    if sig:
        sig_label = ("✅ **Statistically significant** (p < 0.05)" if sig.get("significant")
                     else "❌ **Not statistically significant** (p ≥ 0.05)" if sig.get("significant") is False
                     else "⚠️ Cannot determine (too few pairs, or "
                          "zero-variance deltas)")
        # Use the band eval.stats already computed. Recomputing it here needed
        # abs(cohens_d), which raises on the None that a zero-variance sample
        # now reports, and could drift from stats.effect_label.
        effect_label = sig.get("effect", "unknown")

        def _val(key):
            """Render a metric, distinguishing "absent" from a real number.

            ``sig.get(key, "N/A")`` only defaults on a *missing key*; these keys
            are always present and may hold None, which would print "None".
            """
            value = sig.get(key)
            return "N/A" if value is None else value
        lines += [
            "## Statistical Significance",
            f"_Is the delta ({sig['delta_mean']:+}) meaningful for n={sig['n']} questions?_",
            "",
            f"**Verdict: {sig_label}**",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Paired t-test p-value | {_val('p_ttest')} |",
            f"| Wilcoxon signed-rank p | {_val('p_wilcoxon')} |",
            f"| Cohen's d (effect size) | {_val('cohens_d')} ({effect_label}) |",
            f"| 95% CI on the delta | [{_val('ci_lo')}, {_val('ci_hi')}] "
            f"({_val('ci_method')}) |",
            f"| Delta mean ± std | {sig['delta_mean']:+} ± {sig['delta_std']} |",
            "",
        ]

    # Dynamic vs Static breakdown
    lines += [
        "## Breakdown: Dynamic vs Static",
        "| Set | Count | Context | Baseline | Delta | Improved | Degraded | Neutral |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, s in [
        ("Dynamic (generated)", dyn_stats),
        ("Golden (static)", static_stats),
        ("**Total**", all_stats),
    ]:
        lines.append(
            f'| {label} | {s["count"]} | {s["with_avg"]} | {s["without_avg"]} '
            f'| **{s["delta_avg"]:+}** | {s["improvements"]} | {s["degradations"]} | {s["neutral"]} |'
        )
    lines.append("")

    # Difficulty breakdown
    lines += [
        "## Breakdown by Difficulty",
        "| Difficulty | Count | Context | Baseline | Delta | Improved | Degraded |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for d in DIFFICULTY_ORDER:
        if d in diff_groups:
            s = stats(diff_groups[d])
            lines.append(
                f'| {d} | {s["count"]} | {s["with_avg"]} | {s["without_avg"]} '
                f'| **{s["delta_avg"]:+}** | {s["improvements"]} | {s["degradations"]} |'
            )
    lines.append("")

    # Diagnosis
    diag = Counter(
        e.diagnosis["label"]
        for e in evals
        if e.diagnosis is not None and "label" in e.diagnosis
    )
    total_diag = sum(diag.values())
    lines += [
        "## Diagnosis",
        "| Diagnosis | Count | Rate |",
        "|---|---:|---:|",
    ]
    for label, count in sorted(diag.items(), key=lambda x: -x[1]):
        name = DIAG_MAP.get(label, label)
        rate = round(count / total_diag * 100) if total_diag else 0
        lines.append(f"| {name} | {count} | {rate}% |")
    missing = all_stats["count"] - total_diag
    if missing > 0:
        lines.append(f"| ⚪ Insufficient data | {missing} | {round(missing/all_stats['count']*100)}% |")
    lines.append("")

    # Top 15 helped
    lines += [
        "## Top 15 — Context Helped Most",
        "| QID | Source | Delta | Context | Baseline | Difficulty | Question |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for e in sorted(valid_all, key=lambda x: x.delta or 0, reverse=True)[:15]:
        src = "🔵 static" if static_prefix and e.key.startswith(static_prefix) else "🟡 gen"
        q = fmt_question(e.question_text)
        delta = round(e.delta, 1) if isinstance(e.delta, float) else e.delta
        lines.append(
            f'| {e.question_id} | {src} | **{fmt_delta(delta)}** '
            f'| {e.score(CONTEXT)} | {e.score(BASELINE)} '
            f'| {_difficulty(e, "—")} | {q} |'
        )
    lines.append("")

    # Bottom 15 degradations
    lines += [
        "## Bottom 15 — Biggest Degradations",
        "| QID | Source | Delta | Context | Baseline | Difficulty | Question |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for e in sorted(valid_all, key=lambda x: x.delta or 0)[:15]:
        src = "🔵 static" if static_prefix and e.key.startswith(static_prefix) else "🟡 gen"
        q = fmt_question(e.question_text)
        delta = round(e.delta, 1) if isinstance(e.delta, float) else e.delta
        lines.append(
            f'| {e.question_id} | {src} | **{fmt_delta(delta)}** '
            f'| {e.score(CONTEXT)} | {e.score(BASELINE)} '
            f'| {_difficulty(e, "—")} | {q} |'
        )
    lines.append("")

    # Worst baseline by absolute score
    baseline_sorted = [e for e in evals if e.score(BASELINE) is not None]
    lines += [
        "## Baseline Weak Spots",
        "_Lowest absolute scores for the base model without retrieval context._",
        "",
        "| QID | Source | Baseline score | Context score | Difficulty | Question |",
        "|---|---|---:|---:|---|---|",
    ]
    for e in sorted(baseline_sorted, key=lambda x: x.score(BASELINE) or 0)[:15]:
        src = "🔵 static" if static_prefix and e.key.startswith(static_prefix) else "🟡 gen"
        lines.append(
            f'| {e.question_id} | {src} | **{e.score(BASELINE)}** | {_score_cell(e, CONTEXT)} '
            f'| {_difficulty(e, "—")} | {fmt_question(e.question_text)} |'
        )
    lines.append("")

    # Full Q&A — Static
    lines += [
        "## Full Q&A Table — Golden Static Questions",
        "| QID | Difficulty | Context | Baseline | Delta | Question |",
        "|---|---|---:|---:|---:|---|",
    ]
    for e in sorted(static_qs, key=lambda x: (_difficulty(x, ""), x.key)):
        delta = e.delta
        rounded = round(delta, 1) if isinstance(delta, float) else delta
        lines.append(
            f'| {e.question_id} | {_difficulty(e, "—")} | {_score_cell(e, CONTEXT)} | {_score_cell(e, BASELINE)} '
            f'| {fmt_delta(rounded)} | {fmt_question(e.question_text)} |'
        )
    lines.append("")

    # Full Q&A — Dynamic
    lines += [
        "## Full Q&A Table — Dynamic Generated Questions",
        "| QID | Difficulty | Context | Baseline | Delta | Question |",
        "|---|---|---:|---:|---:|---|",
    ]
    for e in sorted(dynamic_qs, key=lambda x: (_difficulty(x, ""), x.key)):
        delta = e.delta
        rounded = round(delta, 1) if isinstance(delta, float) else delta
        lines.append(
            f'| {e.question_id} | {_difficulty(e, "—")} | {_score_cell(e, CONTEXT)} | {_score_cell(e, BASELINE)} '
            f'| {fmt_delta(rounded)} | {fmt_question(e.question_text)} |'
        )
    lines.append("")

    # Recommendations
    lines += [
        "## Key Observations & Recommendations",
        "",
        f'- **Overall delta: {all_stats["delta_avg"]:+}** — '
        + ("context arm is helping." if all_stats["delta_avg"] > 2 else
           "context arm is neutral or slightly harmful."),
        f'- **Dynamic vs Static agreement:** {dyn_stats["delta_avg"]:+} vs {static_stats["delta_avg"]:+} — '
        + ("good consistency, benchmark is stable." if abs(dyn_stats["delta_avg"] - static_stats["delta_avg"]) < 3
           else "notable gap — investigate question quality."),
        f'- **Win rate:** {round(all_stats["improvements"]/all_stats["count"]*100) if all_stats["count"] else 0}% questions improved with context '
        f'({all_stats["improvements"]}/{all_stats["count"]}).',
        f'- **Degradations:** {all_stats["degradations"]} questions '
        f'({round(all_stats["degradations"]/all_stats["count"]*100) if all_stats["count"] else 0}%).',
        "",
    ]

    report = "\n".join(lines)

    dir_path = os.path.dirname(out_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    # Sidecar JSON: questions + answers + scores
    if qa_json_out is None:
        qa_json_out = out_path.replace(".md", ".qa.json")

    answers_by_id = _answers_by_id(eval_path.replace("/eval/", "/answers/"))

    def _answer_text(a: Record | None, condition: str):
        answer = a.answer(condition) if a is not None else None
        return answer.text if answer is not None else None

    qa_records = []
    for e in evals:
        a = answers_by_id.get(e.key)
        qa_records.append({
            "question_id": e.question_id,
            "question_text": e.question_text or e.raw.get("question_text"),
            "difficulty": e.difficulty,
            "source": "static" if static_prefix and e.key.startswith(static_prefix) else "dynamic",
            "with_docs_score": e.score(CONTEXT),
            "without_docs_score": e.score(BASELINE),
            "delta": e.delta,
            "with_docs_answer": _answer_text(a, CONTEXT),
            "without_docs_answer": _answer_text(a, BASELINE),
        })

    with open(qa_json_out, "w", encoding="utf-8") as jf:
        json.dump({
            "eval_path": eval_path,
            "report_path": out_path,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run_metadata": meta,
            "count": len(qa_records),
            "records": qa_records,
        }, jf, indent=2, ensure_ascii=False)

    print(f"✅ Report written to: {out_path}")
    print(f"✅ Q&A JSON written to: {qa_json_out}")
    print(f"   Questions: {all_stats['count']} ({dyn_stats['count']} dynamic + {static_stats['count']} static)")
    print(f"   Delta: {all_stats['delta_avg']:+} (with={all_stats['with_avg']}, without={all_stats['without_avg']})")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Generate documentation quality report from eval JSON.")
    parser.add_argument("--eval", required=True, help="Path to eval JSON file")
    parser.add_argument("--out", required=True, help="Output path for the markdown report")
    parser.add_argument("--qa-json-out", default=None, help="Optional path for Q&A JSON export")
    args = parser.parse_args()
    generate_report(args.eval, args.out, args.qa_json_out)


if __name__ == "__main__":
    main()
