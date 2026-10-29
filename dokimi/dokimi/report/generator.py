# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Generate reports from evaluation results."""

import json
import logging
from typing import List, Dict, Any, Optional, Sequence
from collections import defaultdict

from dokimi.gate.trust_gate import evaluate_trust, format_trust_block
from dokimi.eval.diagnoser import (
    summarise_diagnoses,
    DIAGNOSIS_LABELS,
    DIAGNOSIS_DESCRIPTIONS,
)
from dokimi.records import BASELINE, CONTEXT, Record, records

#: The persisted condition key each report column reads.
_MODES = {"with_docs": CONTEXT, "without_docs": BASELINE}

logger = logging.getLogger(__name__)


class ReportGenerator:
    """
    Generate analysis reports from eval results.
    
    Features:
    - Topic clustering (by persona + keywords)
    - Top/bottom performers
    - WITH vs WITHOUT comparison by cluster
    - Identify doc gaps
    """
    
    def __init__(self):
        pass
    
    def generate_report(
        self,
        eval_data: Dict[str, Any],
        questions_data: Dict[str, Any],
        output_format: str = "markdown",
        multirun_with_averages: Optional[List[float]] = None,
        trust_thresholds: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate comprehensive report.

        Args:
            eval_data: Loaded eval JSON (from Judge)
            questions_data: Loaded questions JSON (for clustering)
            output_format: "markdown" or "json"
            multirun_with_averages: Per-run context-arm means from N-run mode
            trust_thresholds: Override default trust gate thresholds
        """
        evaluations = records(eval_data.get("evaluations", []), source="evaluations", kind="evaluations")
        questions = questions_data.get("questions", [])
        
        # Build question lookup
        q_lookup = {q["id"]: q for q in questions}
        
        # Overall stats
        stats = self._compute_stats(evaluations)
        
        # Top/bottom performers
        top_with = self._top_performers(evaluations, "with_docs", limit=10)
        bottom_with = self._bottom_performers(evaluations, "with_docs", limit=10)
        
        # Delta analysis
        top_deltas = self._top_deltas(evaluations, limit=10)
        bottom_deltas = self._bottom_deltas(evaluations, limit=10)
        
        # Cluster by topic
        clusters = self._cluster_by_topic(evaluations, q_lookup)

        # Failure analysis
        failure_summary = summarise_diagnoses(evaluations)

        # Trust verdict ("Can we trust this run?")
        trust_verdict = evaluate_trust(
            evaluations,
            thresholds=trust_thresholds,
            multirun_with_averages=multirun_with_averages,
        )

        # Source breakdown
        sources_summary = self._compute_source_summary(evaluations, q_lookup)

        # Evaluator-independence warning (non-fatal)
        temp_meta = eval_data.get("run_metadata") if isinstance(eval_data, dict) else None
        run_meta = temp_meta if isinstance(temp_meta, dict) else {}
        answer_model = run_meta.get("answer_model")
        answer_provider = run_meta.get("answer_provider")
        judge_model = run_meta.get("judge_model")
        judge_provider = run_meta.get("judge_provider")
        question_set_hash = run_meta.get("question_set_hash")
        evaluator_independence_warning = bool(
            run_meta.get("evaluator_independence_warning")
            or (
                answer_model and answer_provider and judge_model and judge_provider
                and str(answer_model).strip().lower() == str(judge_model).strip().lower()
                and str(answer_provider).strip().lower() == str(judge_provider).strip().lower()
            )
        )

        if output_format == "json":
            return json.dumps({
                "stats": stats,
                "top_with_docs": top_with,
                "bottom_with_docs": bottom_with,
                "top_deltas": top_deltas,
                "bottom_deltas": bottom_deltas,
                "clusters": clusters,
                "sources_summary": sources_summary,
                "failure_analysis": failure_summary,
                "trust": {
                    "verdict": trust_verdict.status,
                    "trusted": trust_verdict.is_trusted,
                    # Read with "verdict": a PASS with entries here passed without
                    # running those checks (the novelty gate's as_dict carries the same).
                    "not_measured": trust_verdict.not_measured,
                    "checks": [
                        {"name": c.name, "passed": c.passed, "value": c.value,
                         "threshold": c.threshold, "message": c.message, "severity": c.severity,
                         "measured": c.measured}
                        for c in trust_verdict.checks
                    ],
                },
                "warnings": {
                    "evaluator_independence": evaluator_independence_warning,
                    "answer_model": answer_model,
                    "answer_provider": answer_provider,
                    "judge_model": judge_model,
                    "judge_provider": judge_provider,
                    "question_set_hash": question_set_hash,
                },
            }, indent=2)
        else:
            return self._format_markdown(
                stats, top_with, bottom_with, top_deltas, bottom_deltas,
                clusters, failure_summary,
                sources_summary=sources_summary,
                evaluator_independence_warning=evaluator_independence_warning,
                answer_model=answer_model,
                answer_provider=answer_provider,
                judge_model=judge_model,
                judge_provider=judge_provider,
                question_set_hash=question_set_hash,
                trust_verdict=trust_verdict,
            )
    
    def _compute_stats(self, evaluations: Sequence[Record]) -> Dict[str, Any]:
        """Compute overall statistics."""
        with_scores, without_scores, deltas = _columns(evaluations)
        
        return {
            "total_questions": len(evaluations),
            "with_docs": {
                "avg": round(sum(with_scores) / len(with_scores), 1) if with_scores else 0,
                "min": round(min(with_scores), 1) if with_scores else 0,
                "max": round(max(with_scores), 1) if with_scores else 0,
            },
            "without_docs": {
                "avg": round(sum(without_scores) / len(without_scores), 1) if without_scores else 0,
                "min": round(min(without_scores), 1) if without_scores else 0,
                "max": round(max(without_scores), 1) if without_scores else 0,
            },
            "delta": {
                "avg": round(sum(deltas) / len(deltas), 1) if deltas else 0,
                "min": round(min(deltas), 1) if deltas else 0,
                "max": round(max(deltas), 1) if deltas else 0,
            },
            "doc_improvements": sum(1 for d in deltas if d > 0),
            "doc_degradations": sum(1 for d in deltas if d < 0),
        }
    
    def _top_performers(self, evaluations: Sequence[Record], mode: str, limit: int) -> List[Dict]:
        """Get top N by aggregate score."""
        scored = [(s, e) for e in evaluations if (s := e.score(_MODES[mode])) is not None]
        scored.sort(reverse=True, key=lambda x: x[0])
        return [
            {
                "question_id": e.question_id,
                "score": s,
                "question_text": e.question_text[:80]
            }
            for s, e in scored[:limit]
        ]
    
    def _bottom_performers(self, evaluations: Sequence[Record], mode: str, limit: int) -> List[Dict]:
        """Get bottom N by aggregate score."""
        scored = [(s, e) for e in evaluations if (s := e.score(_MODES[mode])) is not None]
        scored.sort(key=lambda x: x[0])
        return [
            {
                "question_id": e.question_id,
                "score": s,
                "question_text": e.question_text[:80]
            }
            for s, e in scored[:limit]
        ]
    
    def _top_deltas(self, evaluations: Sequence[Record], limit: int) -> List[Dict]:
        """Get top N improvements (WITH - WITHOUT)."""
        # Both arms scored: a delta next to an unscored arm has no row to show.
        deltas = [(d, e) for e in evaluations if (d := e.delta) is not None
                  and e.score(CONTEXT) is not None and e.score(BASELINE) is not None]
        deltas.sort(reverse=True, key=lambda x: x[0])
        return [
            {
                "question_id": e.question_id,
                "delta": d,
                "with_score": e.score(CONTEXT),
                "without_score": e.score(BASELINE),
                "question_text": e.question_text[:80]
            }
            for d, e in deltas[:limit]
        ]
    
    def _bottom_deltas(self, evaluations: Sequence[Record], limit: int) -> List[Dict]:
        """Get bottom N (worst degradations from docs)."""
        # Both arms scored: a delta next to an unscored arm has no row to show.
        deltas = [(d, e) for e in evaluations if (d := e.delta) is not None
                  and e.score(CONTEXT) is not None and e.score(BASELINE) is not None]
        deltas.sort(key=lambda x: x[0])
        return [
            {
                "question_id": e.question_id,
                "delta": d,
                "with_score": e.score(CONTEXT),
                "without_score": e.score(BASELINE),
                "question_text": e.question_text[:80]
            }
            for d, e in deltas[:limit]
        ]
    
    def _cluster_by_topic(
        self,
        evaluations: Sequence[Record],
        q_lookup: Dict[Any, Dict]
    ) -> List[Dict[str, Any]]:
        """
        Cluster questions by topic (extracted from persona + keywords).
        
        Returns list of clusters with stats.
        """
        # Extract topics from questions
        topic_map = defaultdict(list)
        
        for e in evaluations:
            q = q_lookup.get(e.question_id, {})
            
            # Use persona as primary cluster
            persona_id = q.get("persona_id", "unknown")
            topic_map[persona_id].append(e)
        
        # Compute stats per cluster
        clusters = []
        for topic, evals in topic_map.items():
            with_scores, without_scores, deltas = _columns(evals)
            
            clusters.append({
                "topic": topic,
                "count": len(evals),
                "with_avg": round(sum(with_scores) / len(with_scores), 1) if with_scores else 0,
                "without_avg": round(sum(without_scores) / len(without_scores), 1) if without_scores else 0,
                "delta_avg": round(sum(deltas) / len(deltas), 1) if deltas else 0,
            })
        
        # Sort by delta (worst first)
        clusters.sort(key=lambda x: x["delta_avg"])
        
        return clusters
    
    def _compute_source_summary(self, evaluations: Sequence[Record], q_lookup: Dict[Any, Dict]) -> List[Dict[str, Any]]:
        """Compute performance broken down by question source_type (generated vs manual)."""
        source_map = defaultdict(list)
        for e in evaluations:
            q = q_lookup.get(e.question_id, {})
            # Default to unknown if not present
            stype = q.get("source_type", "unknown")
            source_map[stype].append(e)

        results = []
        for stype, evals in source_map.items():
            with_scores, without_scores, deltas = _columns(evals)

            results.append({
                "source_type": stype,
                "count": len(evals),
                "with_avg": round(sum(with_scores) / len(with_scores), 1) if with_scores else 0,
                "without_avg": round(sum(without_scores) / len(without_scores), 1) if without_scores else 0,
                "delta_avg": round(sum(deltas) / len(deltas), 1) if deltas else 0,
            })
            
        results.sort(key=lambda x: x["source_type"])
        return results

    def _format_markdown(
        self,
        stats: Dict,
        top_with: List[Dict],
        bottom_with: List[Dict],
        top_deltas: List[Dict],
        bottom_deltas: List[Dict],
        clusters: List[Dict],
        failure_summary: Optional[Dict] = None,
        sources_summary: Optional[List[Dict]] = None,
        evaluator_independence_warning: bool = False,
        answer_model: Optional[str] = None,
        answer_provider: Optional[str] = None,
        judge_model: Optional[str] = None,
        judge_provider: Optional[str] = None,
        question_set_hash: Optional[str] = None,
        trust_verdict=None,
    ) -> str:
        """Format report as Markdown."""
        lines = [
            "# Documentation Quality Report",
            "",
            f"**Total Questions:** {stats['total_questions']}",
            f"**Question Set ID:** `{question_set_hash}`" if question_set_hash else "**Question Set ID:** _(not recorded)_",
            "",
        ]

        # Trust block — placed right after header, before stats
        if trust_verdict is not None:
            lines.append(format_trust_block(trust_verdict))

        lines += [
            "## Overall Statistics",
            "",
            "| Metric | Context Arm | Baseline | Delta |",
            "|--------|-----------|--------------|-------|",
            f"| Average | {stats['with_docs']['avg']} | {stats['without_docs']['avg']} | **{stats['delta']['avg']:+.1f}** |",
            f"| Min | {stats['with_docs']['min']} | {stats['without_docs']['min']} | {stats['delta']['min']:+.1f} |",
            f"| Max | {stats['with_docs']['max']} | {stats['without_docs']['max']} | {stats['delta']['max']:+.1f} |",
            "",
            f"- **Improvements:** {stats['doc_improvements']} questions (context helped)",
            f"- **Degradations:** {stats['doc_degradations']} questions (context hurt)",
            "",
        ]

        if evaluator_independence_warning:
            answer_provider_safe = answer_provider or "unknown"
            answer_model_safe = answer_model or "unknown"
            judge_provider_safe = judge_provider or "unknown"
            judge_model_safe = judge_model or "unknown"
            lines.extend([
                "## ⚠️ Evaluation Integrity Warning",
                "",
                "Answering and judging used the same model/provider. This can bias scores (self-referential evaluation risk).",
                "",
                f"- **Answer model:** {answer_provider_safe}/{answer_model_safe}",
                f"- **Judge model:** {judge_provider_safe}/{judge_model_safe}",
                "- **Status:** run completed, but interpret results with caution.",
                "",
            ])

        lines.extend([
            "---",
            "",
            "## Top 10: Best Context-Arm Performance",
            "",
            "| Question ID | Score | Question Text |",
            "|-------------|-------|---------------|",
        ])
        
        for item in top_with:
            lines.append(f"| {item['question_id']} | {item['score']:.1f} | {item['question_text']}... |")
        
        lines.extend([
            "",
            "---",
            "",
            "## Bottom 10: Worst Context-Arm Performance",
            "",
            "| Question ID | Score | Question Text |",
            "|-------------|-------|---------------|",
        ])
        
        for item in bottom_with:
            lines.append(f"| {item['question_id']} | {item['score']:.1f} | {item['question_text']}... |")
        
        lines.extend([
            "",
            "---",
            "",
            "## Top 10: Biggest Improvements (context helped most)",
            "",
            "| Question ID | Delta | Context Arm | Baseline | Question Text |",
            "|-------------|-------|------|---------|---------------|",
        ])
        
        for item in top_deltas:
            lines.append(
                f"| {item['question_id']} | **+{item['delta']:.1f}** | "
                f"{item['with_score']:.1f} | {item['without_score']:.1f} | "
                f"{item['question_text']}... |"
            )
        
        lines.extend([
            "",
            "---",
            "",
            "## Bottom 10: Biggest Degradations (context hurt most)",
            "",
            "| Question ID | Delta | Context Arm | Baseline | Question Text |",
            "|-------------|-------|------|---------|---------------|",
        ])
        
        for item in bottom_deltas:
            lines.append(
                f"| {item['question_id']} | **{item['delta']:.1f}** | "
                f"{item['with_score']:.1f} | {item['without_score']:.1f} | "
                f"{item['question_text']}... |"
            )
        
        lines.extend([
            "",
            "---",
            "",
            "## Performance by Topic/Persona",
            "",
            "| Topic | Count | Context Arm Avg | Baseline Avg | Delta |",
            "|-------|-------|----------|-------------|-------|",
        ])
        
        for cluster in clusters:
            lines.append(
                f"| {cluster['topic']} | {cluster['count']} | "
                f"{cluster['with_avg']:.1f} | {cluster['without_avg']:.1f} | "
                f"**{cluster['delta_avg']:+.1f}** |"
            )
            
        # ── Source Breakdown ─────────────────────────────────────────────
        if sources_summary:
            lines.extend([
                "",
                "---",
                "",
                "## Performance by Question Source",
                "",
                "| Source Type | Count | Context Arm Avg | Baseline Avg | Delta |",
                "|-------------|-------|----------|-------------|-------|",
            ])
            for src in sources_summary:
                lines.append(
                    f"| {src['source_type']} | {src['count']} | "
                    f"{src['with_avg']:.1f} | {src['without_avg']:.1f} | "
                    f"**{src['delta_avg']:+.1f}** |"
                )
        
        # ── Failure Analysis section ─────────────────────────────────────
        if failure_summary:
            lines.extend([
                "",
                "---",
                "",
                "## Failure Analysis",
                "",
                "Breakdown of *why* documentation helped or failed for each question.",
                "",
                "| Diagnosis | Count | Rate | Description |",
                "|-----------|------:|-----:|-------------|",
            ])
            counts = failure_summary.get("counts", {})
            total_q = failure_summary.get("total", 1)
            for label, display in DIAGNOSIS_LABELS.items():
                count = counts.get(label, 0)
                rate = f"{count / total_q:.0%}" if total_q else "n/a"  # the pipeline may judge 0 answers
                desc = DIAGNOSIS_DESCRIPTIONS.get(label, "")
                lines.append(f"| {display} | {count} | {rate} | {desc} |")

            # Detail table for failures (delta < 0)
            failures = failure_summary.get("failures", [])
            if failures:
                lines.extend([
                    "",
                    f"### Questions with Negative Delta ({len(failures)} total)",
                    "",
                    "| Question ID | Delta | Diagnosis | Evidence |",
                    "|-------------|------:|-----------|---------|",
                ])
                for e in sorted(failures, key=lambda x: x.get("delta") or 0)[:20]:
                    diag = e.get("diagnosis") or {}
                    label = diag.get("label", "?")
                    display = DIAGNOSIS_LABELS.get(label, label)
                    evidence = diag.get("evidence", {})
                    ev_str = ", ".join(
                        f"{k}={v}" for k, v in evidence.items()
                        if k != "delta" and v is not None
                    )
                    lines.append(
                        f"| {e['question_id']} | **{(e.get('delta') or 0):+.1f}** "
                        f"| {display} | {ev_str} |"
                    )

        lines.extend([
            "",
            "---",
            "",
            "## Recommendations",
            "",
        ])
        
        # Add recommendations based on data
        if stats["delta"]["avg"] < 0:
            lines.append("⚠️ **Overall:** Documentation is hurting answer quality. Consider:")
            lines.append("- Improving retrieval relevance")
            lines.append("- Adding more specific examples to docs")
            lines.append("- Removing outdated/misleading content")
        elif stats["delta"]["avg"] > 2:
            lines.append("✅ **Overall:** Documentation is significantly helping. Continue:")
            lines.append("- Maintaining doc quality")
            lines.append("- Expanding coverage for low-performing topics")
        else:
            lines.append("⚪ **Overall:** Documentation has minimal impact. Consider:")
            lines.append("- Improving doc discoverability")
            lines.append("- Adding more practical examples")
        
        lines.append("")
        
        # Identify worst clusters
        worst_clusters = [c for c in clusters if c["delta_avg"] < -1]
        if worst_clusters:
            lines.append("### Topics Needing Doc Improvements:")
            lines.append("")
            for c in worst_clusters[:5]:
                lines.append(f"- **{c['topic']}** (delta: {c['delta_avg']:+.1f})")
        
        return "\n".join(lines)


def _columns(evaluations: Sequence[Record]) -> "tuple[List[float], List[float], List[float]]":
    """Each arm's scores and the persisted deltas, each over the records that have one.

    The arms are averaged over different question sets when a question is scored
    in one arm only. The deltas are the persisted ``delta`` values, whatever the
    arms' scores: a record with a delta and one unscored arm counts in the delta
    mean but not in that arm's, so the delta mean need not equal the difference
    of the arm means (and the top/bottom tables, which need both arms, leave it out).
    """
    with_scores = [s for e in evaluations if (s := e.score(CONTEXT)) is not None]
    without_scores = [s for e in evaluations if (s := e.score(BASELINE)) is not None]
    deltas = [d for e in evaluations if (d := e.delta) is not None]
    return with_scores, without_scores, deltas
