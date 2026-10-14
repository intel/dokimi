# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for trust gate — 'Can we trust this run?' check."""
from dokimi.gate.trust_gate import (
    evaluate_trust,
    format_trust_block,
)


def _make_eval(with_score=70.0, without_score=50.0, agree=0.8):
    return {
        "question_id": "q_001",
        "with_docs": {"aggregate": with_score, "agreement_score": agree},
        "without_docs": {"aggregate": without_score},
        "delta": with_score - without_score,
    }


def _evals(n=20, with_score=70.0, without_score=50.0):
    return [_make_eval(with_score, without_score) for _ in range(n)]


class TestMinQuestions:
    def test_passes_with_enough(self):
        v = evaluate_trust(_evals(20))
        check = next(c for c in v.checks if c.name == "min_questions")
        assert check.passed

    def test_fails_with_too_few(self):
        v = evaluate_trust(_evals(5))
        check = next(c for c in v.checks if c.name == "min_questions")
        assert not check.passed
        assert check.severity == "fail"

    def test_empty_evals_only_min_q_check(self):
        v = evaluate_trust([])
        assert len(v.checks) == 1
        assert not v.checks[0].passed


class TestZeroScoreFraction:
    def test_passes_low_zero_fraction(self):
        evals = _evals(20, with_score=70)
        evals[0]["with_docs"]["aggregate"] = 0
        v = evaluate_trust(evals)
        check = next(c for c in v.checks if c.name == "zero_score_fraction")
        assert check.passed  # 1/20 = 5%

    def test_fails_high_zero_fraction(self):
        evals = _evals(10, with_score=0)  # all zeros
        v = evaluate_trust(evals)
        check = next(c for c in v.checks if c.name == "zero_score_fraction")
        assert not check.passed


class TestWithDocsAvg:
    def test_passes_above_threshold(self):
        v = evaluate_trust(_evals(15, with_score=60))
        check = next(c for c in v.checks if c.name == "min_with_docs_avg")
        assert check.passed

    def test_warns_below_threshold(self):
        v = evaluate_trust(_evals(15, with_score=20))
        check = next(c for c in v.checks if c.name == "min_with_docs_avg")
        assert not check.passed
        assert check.severity == "warn"


class TestDeltaCheck:
    def test_passes_positive_delta(self):
        v = evaluate_trust(_evals(15, with_score=70, without_score=50))
        check = next(c for c in v.checks if c.name == "min_delta")
        assert check.passed

    def test_warns_on_large_negative_delta(self):
        v = evaluate_trust(_evals(15, with_score=30, without_score=60))
        check = next(c for c in v.checks if c.name == "min_delta")
        assert not check.passed


class TestMultiRunVariance:
    def test_low_variance_passes(self):
        v = evaluate_trust(_evals(15), multirun_with_averages=[70.0, 71.0, 70.5])
        check = next(c for c in v.checks if c.name == "multirun_variance")
        assert check.passed

    def test_high_variance_fails(self):
        v = evaluate_trust(_evals(15), multirun_with_averages=[70.0, 50.0, 90.0])
        check = next(c for c in v.checks if c.name == "multirun_variance")
        assert not check.passed
        assert check.severity == "fail"

    def test_single_run_no_variance_check(self):
        v = evaluate_trust(_evals(15), multirun_with_averages=[70.0])
        names = [c.name for c in v.checks]
        assert "multirun_variance" not in names


class TestTrustVerdict:
    def test_trusted_all_pass(self):
        v = evaluate_trust(_evals(20, with_score=70, without_score=50))
        assert v.is_trusted

    def test_not_trusted_fail_severity(self):
        v = evaluate_trust(_evals(5))  # fails min_questions (fail severity)
        assert not v.is_trusted

    def test_status_pass(self):
        v = evaluate_trust(_evals(20, with_score=70, without_score=50))
        assert v.status == "✅ PASS"

    def test_status_warn(self):
        v = evaluate_trust(_evals(15, with_score=20, without_score=15))
        # with_avg=20 below min 30 → warn; no fail severities
        assert "WARN" in v.status or "FAIL" in v.status


class TestCustomThresholds:
    def test_custom_min_questions(self):
        v = evaluate_trust(_evals(5), thresholds={"min_questions": 3})
        check = next(c for c in v.checks if c.name == "min_questions")
        assert check.passed

    def test_custom_max_zero_fraction(self):
        evals = _evals(10, with_score=0)
        v = evaluate_trust(evals, thresholds={"max_zero_fraction": 1.0})
        check = next(c for c in v.checks if c.name == "zero_score_fraction")
        assert check.passed


class TestFormatTrustBlock:
    def test_renders_markdown(self):
        v = evaluate_trust(_evals(20, with_score=70, without_score=50))
        md = format_trust_block(v)
        assert "## Can We Trust This Run?" in md
        assert "✅ PASS" in md or "⚠️ WARN" in md or "❌ FAIL" in md
        assert "| Check |" in md
        assert "Context-arm avg" in md
        assert ("WITH" + "-docs") not in md
        assert ("WITH" + "−" + "WITHOUT") not in md

    def test_shows_failures(self):
        v = evaluate_trust(_evals(5))  # fails min_questions
        md = format_trust_block(v)
        assert "Blocking issues" in md
        assert "min_questions" in md


class TestRetrievalHitRate:
    """Nothing wrote the ``with_docs.retrieval_hit`` the check read, so it reduced to
    "context-arm score > 0" and passed on runs where retrieval never returned a doc."""

    @staticmethod
    def _judged(n_hit, n_miss):
        from unittest.mock import MagicMock

        from dokimi.eval.judge import Judge

        reply = MagicMock(content=(
            '{"correctness": 60, "completeness": 60, "specificity": 60, '
            '"code_quality": 60, "actionability": 60, "aggregate": 0}'
        ))
        judge = Judge.__new__(Judge)
        judge._accumulate_usage = lambda usage: None
        judge.temperature = judge.seed = None
        judge.llm = MagicMock()
        judge.llm.invoke.return_value = reply
        hit = {"answer": "use parallel_for", "doc_source": "context7",
               "retrieved_docs": [{"source": "context7", "snippet": "parallel_for ...",
                                   "relevance_score": 0.8}]}
        # What Answerer writes when retrieval or reranking returned nothing.
        miss = {"answer": "[FALLBACK: No relevant documentation found]",
                "doc_source": "fallback_none", "retrieved_docs": []}
        return [
            judge._evaluate_answer_pair("L", {
                "question_id": f"q{i}", "question_text": "q?",
                "with_docs": dict(hit if i < n_hit else miss),
                "without_docs": {"answer": "baseline"},
            })
            for i in range(n_hit + n_miss)
        ]

    def test_fails_when_retrieval_never_returned_a_doc(self):
        evals = self._judged(n_hit=0, n_miss=12)
        assert all(e["with_docs"]["aggregate"] > 0 for e in evals)
        check = next(c for c in evaluate_trust(evals).checks if c.name == "retrieval_hit_rate")
        assert not check.passed, check.message
        assert check.value == 0.0
        assert check.measured

    def test_rate_counts_recorded_hits(self):
        evals = self._judged(n_hit=9, n_miss=3)
        check = next(c for c in evaluate_trust(evals).checks if c.name == "retrieval_hit_rate")
        assert (check.value, check.passed) == (0.75, True)

    def test_legacy_artifact_is_not_measured_not_passed(self):
        v = evaluate_trust(_evals(20))
        check = next(c for c in v.checks if c.name == "retrieval_hit_rate")
        assert not check.measured
        assert check.value is None
        assert check not in v.warnings and v.is_trusted
        row = next(line for line in format_trust_block(v).splitlines()
                   if line.startswith("| retrieval_hit_rate"))
        assert "✅" not in row and "Not measured" in row

    def test_verdict_line_counts_unmeasured_checks(self):
        v = evaluate_trust(_evals(20))
        assert v.not_measured == ["retrieval_hit_rate"]
        verdict = next(line for line in format_trust_block(v).splitlines()
                       if line.startswith("**Verdict:"))
        assert verdict.endswith(f"(1 of {len(v.checks)} not measured)")

    def test_low_coverage_is_not_measured(self):
        """5 recorded hits among 20 evaluations must not read as a 100% hit rate."""
        v = evaluate_trust(self._judged(n_hit=5, n_miss=0) + _evals(15))
        check = next(c for c in v.checks if c.name == "retrieval_hit_rate")
        assert not check.measured
        assert check.value is None
        assert "5 of 20" in check.message and "coverage floor" in check.message
        assert v.not_measured == ["retrieval_hit_rate"]
        verdict = next(line for line in format_trust_block(v).splitlines()
                       if line.startswith("**Verdict:"))
        assert verdict.endswith(f"(1 of {len(v.checks)} not measured)")

    def test_coverage_at_floor_is_measured(self):
        v = evaluate_trust(self._judged(n_hit=12, n_miss=4) + _evals(4))
        check = next(c for c in v.checks if c.name == "retrieval_hit_rate")
        assert check.measured
        assert check.value == 0.75
        assert "4 unrecorded" in check.message

    def test_report_json_lists_not_measured_checks(self):
        import json

        from dokimi.report.generator import ReportGenerator

        report = json.loads(ReportGenerator().generate_report(
            {"evaluations": _evals(20)}, {"questions": []}, output_format="json",
        ))
        assert report["trust"]["verdict"] == "✅ PASS"
        assert report["trust"]["not_measured"] == ["retrieval_hit_rate"]
