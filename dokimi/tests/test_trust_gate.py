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
        evals = self._judged(n_hit=0, n_miss=14)
        # Every question is an unjudged miss: nothing is scored in both arms, the run is not
        # trusted, and the message names the cause (0 of 14 hit) beside the question count.
        verdict = evaluate_trust(evals)
        assert not verdict.is_trusted
        assert [c.name for c in verdict.failures] == ["min_questions"]
        check = next(c for c in verdict.checks if c.name == "retrieval_hit_rate")
        assert not check.passed and check.measured and check.value == 0.0
        assert "(0/14)" in check.message

    def test_fails_when_retrieval_rarely_returned_a_doc(self):
        evals = self._judged(n_hit=2, n_miss=12)
        # The placeholder is not judged by default: only the two hits carry a with-docs score,
        # and the twelve misses still count against the rate.
        assert sum(e["with_docs"] is None for e in evals) == 12
        check = next(c for c in evaluate_trust(evals).checks if c.name == "retrieval_hit_rate")
        assert not check.passed, check.message
        assert check.value == round(2 / 14, 3)
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


def _row(with_score, without_score, *, agree=None, hit=None):
    ev = {"question_id": "q", "with_docs": {"aggregate": with_score},
          "without_docs": {"aggregate": without_score}}
    if agree is not None:
        ev["with_docs"]["agreement_score"] = agree
    if hit is not None:
        ev["retrieval_hit"] = hit
    return ev


def _check(verdict, name):
    return next(c for c in verdict.checks if c.name == name)


class TestEveryCheckReportsItsNumbers:
    """One small run, every check's value worked out by hand.

    Context arm 0, 40, 60 (mean 33.33, sample sd 30.55); baseline 10, 20, 30;
    panel agreement 0.1, 0.2, 0.4; two of three questions retrieved a doc; two
    runs averaging 40 and 45 (sd 3.536).
    """

    def test_values_thresholds_and_severities(self):
        evals = [
            _row(0, 10, agree=0.1, hit=True),
            _row(40, 20, agree=0.2, hit=True),
            _row(60, 30, agree=0.4, hit=False),
        ]

        v = evaluate_trust(evals, multirun_with_averages=[40.0, 45.0])

        assert [(c.name, c.passed, c.value, c.threshold, c.severity, c.measured)
                for c in v.checks] == [
            ("min_questions", False, 3, 10, "fail", True),
            ("zero_score_fraction", False, 0.333, 0.20, "fail", True),
            ("min_with_docs_avg", True, 33.3, 30.0, "warn", True),
            ("min_delta", True, 13.3, -5.0, "warn", True),
            ("score_cv", False, 0.917, 0.60, "warn", True),
            ("inter_rater_agreement", False, 0.233, 0.50, "warn", True),
            ("retrieval_hit_rate", True, 0.667, 0.60, "warn", True),
            ("multirun_variance", True, 3.54, 5.0, "fail", True),
        ]
        assert round(v.multirun_std, 4) == 3.5355
        assert [c.name for c in v.failures] == ["min_questions", "zero_score_fraction"]
        assert [c.name for c in v.warnings] == ["score_cv", "inter_rater_agreement"]


class TestEveryThresholdIsInclusive:
    """A value exactly at its threshold passes, for every check."""

    def test_min_questions_at_floor(self):
        assert _check(evaluate_trust(_evals(10)), "min_questions").passed

    def test_zero_fraction_at_ceiling(self):
        evals = [_row(0, 0)] * 2 + [_row(70, 50)] * 8
        assert _check(evaluate_trust(evals), "zero_score_fraction").passed

    def test_context_average_at_floor(self):
        check = _check(evaluate_trust(_evals(10, with_score=30, without_score=20)), "min_with_docs_avg")
        assert (check.value, check.passed) == (30.0, True)

    def test_delta_at_floor(self):
        check = _check(evaluate_trust(_evals(10, with_score=50, without_score=55)), "min_delta")
        assert (check.value, check.passed) == (-5.0, True)

    def test_cv_at_ceiling(self):
        v = evaluate_trust(_evals(10), thresholds={"max_cv": 0.0})
        check = _check(v, "score_cv")
        assert (check.value, check.passed) == (0.0, True)

    def test_agreement_at_floor(self):
        evals = [_row(70, 50, agree=0.5)] * 10
        check = _check(evaluate_trust(evals), "inter_rater_agreement")
        assert (check.value, check.passed) == (0.5, True)

    def test_one_agreement_score_among_many_rows_is_still_judged(self):
        evals = [_row(70, 50)] * 10 + [_row(70, 50, agree=0.1)]
        check = _check(evaluate_trust(evals), "inter_rater_agreement")
        assert (check.passed, check.value) == (False, 0.1)

    def test_hit_rate_at_floor(self):
        evals = [_row(70, 50, hit=True)] * 6 + [_row(70, 50, hit=False)] * 4
        check = _check(evaluate_trust(evals), "retrieval_hit_rate")
        assert (check.value, check.passed) == (0.6, True)

    def test_multirun_std_at_ceiling(self):
        v = evaluate_trust(_evals(10), thresholds={"max_multirun_std": 0.0},
                           multirun_with_averages=[50.0, 50.0])
        check = _check(v, "multirun_variance")
        assert (check.value, check.passed) == (0.0, True)


class TestWhatTheChecksCount:
    def test_a_question_scored_in_one_arm_only_is_not_counted(self):
        evals = _evals(10) + [{"question_id": "q", "with_docs": {"aggregate": 90}}]
        assert _check(evaluate_trust(evals), "min_questions").value == 10

    def test_a_question_scored_in_the_baseline_only_is_not_counted(self):
        # The other half of the both-arms filter: a baseline score with no context score
        # would otherwise count, and its missing context score read as 0.
        evals = _evals(10) + [{"question_id": "q", "without_docs": {"aggregate": 50}}]
        v = evaluate_trust(evals)
        assert _check(v, "min_questions").value == 10
        assert _check(v, "zero_score_fraction").value == 0.0

    def test_coverage_below_the_default_floor_is_not_measured(self):
        # 15 of 20 record a retrieval outcome: 75%, under the 80% default floor.
        v = evaluate_trust([_row(70, 50, hit=True)] * 15 + [_row(70, 50)] * 5)
        assert not _check(v, "retrieval_hit_rate").measured
        assert v.not_measured == ["retrieval_hit_rate"]

    def test_a_zero_baseline_counts_as_zero(self):
        check = _check(evaluate_trust(_evals(10, with_score=40, without_score=0)), "min_delta")
        assert check.value == 40.0

    def test_two_runs_are_enough_for_the_variance_check(self):
        v = evaluate_trust(_evals(10), multirun_with_averages=[50.0, 70.0])
        check = _check(v, "multirun_variance")
        assert (check.value, check.passed) == (14.14, False)


class TestScoreCv:
    def test_one_question_has_no_spread(self):
        check = _check(evaluate_trust([_row(50, 40)], thresholds={"min_questions": 1}), "score_cv")
        assert (check.value, check.passed) == (0.0, True)

    def test_two_questions_use_the_sample_sd(self):
        # sd(40, 60) = 14.142, mean 50
        check = _check(evaluate_trust([_row(40, 0), _row(60, 0)]), "score_cv")
        assert check.value == 0.283

    def test_a_mean_below_one_is_still_divided(self):
        # sd(0.25, 0.75) = 0.354 over a mean of 0.5
        check = _check(evaluate_trust([_row(0.25, 0), _row(0.75, 0)]), "score_cv")
        assert check.value == 0.707

    def test_an_all_zero_context_arm_reads_as_cv_one(self):
        check = _check(evaluate_trust(_evals(10, with_score=0)), "score_cv")
        assert (check.value, check.passed) == (1.0, False)


def test_a_not_measured_retrieval_check_never_warns():
    """Under the coverage floor the rate is not judged: it passes, is not a
    warning, and serializes ``measured`` as false (the report JSON reads it)."""
    evals = [_row(70, 50, hit=False)] * 2 + [_row(70, 50)] * 8

    v = evaluate_trust(evals)
    check = _check(v, "retrieval_hit_rate")

    assert (check.passed, check.measured, check.value) == (True, False, None)
    assert (check.threshold, check.severity) == (0.60, "warn")
    assert check not in v.warnings
    assert v.status == "✅ PASS"


def test_a_legacy_artifact_serializes_measured_false():
    import json

    from dokimi.report.generator import ReportGenerator

    check = _check(evaluate_trust(_evals(10)), "retrieval_hit_rate")
    assert (check.threshold, check.severity) == (0.60, "warn")
    assert (check.passed, check.measured) == (True, False)

    # The report JSON is what reads "measured", so check the key there.
    report = json.loads(ReportGenerator().generate_report(
        {"evaluations": _evals(10)}, {"questions": []}, output_format="json",
    ))
    (rate,) = [c for c in report["trust"]["checks"] if c["name"] == "retrieval_hit_rate"]
    assert (rate["passed"], rate["measured"], rate["severity"]) == (True, False, "warn")


class TestTheRenderedBlock:
    """The block a report shows, whole: each status gets its icon, each list its checks."""

    def test_failures_and_warnings_render_apart(self):
        evals = [
            _row(0, 10, agree=0.1, hit=True),
            _row(40, 20, agree=0.2, hit=True),
            _row(60, 30, agree=0.4, hit=False),
        ]
        md = format_trust_block(evaluate_trust(evals, multirun_with_averages=[40.0, 45.0]))
        assert md == "\n".join([
            "## Can We Trust This Run?",
            "",
            "**Verdict: ❌ FAIL**",
            "",
            "| Check | Status | Value | Threshold | Notes |",
            "|-------|--------|-------|-----------|-------|",
            "| min_questions | ❌ | 3 | 10 | 3 questions evaluated (min 10) |",
            "| zero_score_fraction | ❌ | 0.333 | 0.2 | 1/3 questions scored 0 in the context arm (33.3%) |",
            "| min_with_docs_avg | ✅ | 33.3 | 30.0 | Context-arm avg = 33.3 (min 30.0) |",
            "| min_delta | ✅ | 13.3 | -5.0 | Avg delta (context arm minus baseline) = +13.3 (min -5.0) |",
            "| score_cv | ⚠️ | 0.917 | 0.6 | Score CV = 0.92 std=30.6 (max CV 0.6) |",
            "| inter_rater_agreement | ⚠️ | 0.233 | 0.5 | Mean inter-rater agreement = 0.23 (min 0.5) |",
            "| retrieval_hit_rate | ✅ | 0.667 | 0.6 | Retrieval hit rate = 66.7% (2/3) (min 60%) |",
            "| multirun_variance | ✅ | 3.54 | 5.0 | Multi-run context-arm std = 3.54 across 2 runs (max 5.0) |",
            "",
            "**Blocking issues:**",
            "- ❌ **min_questions**: 3 questions evaluated (min 10)",
            "- ❌ **zero_score_fraction**: 1/3 questions scored 0 in the context arm (33.3%)",
            "",
            "**Warnings:**",
            "- ⚠️ **score_cv**: Score CV = 0.92 std=30.6 (max CV 0.6)",
            "- ⚠️ **inter_rater_agreement**: Mean inter-rater agreement = 0.23 (min 0.5)",
            "",
        ])

    def test_a_not_measured_check_renders_as_neither_pass_nor_fail(self):
        md = format_trust_block(evaluate_trust([_row(70, 50)] * 10))
        lines = md.splitlines()
        assert lines[2] == "**Verdict: ✅ PASS** (1 of 6 not measured)"
        assert lines[-1] == (
            "| retrieval_hit_rate | ➖ | None | 0.6 | Not measured: the evaluations "
            "record no retrieval outcome (retrieval_hit) |"
        )
        assert "Warnings" not in md

    def test_the_hit_rate_names_the_unrecorded_evaluations(self):
        v = evaluate_trust([_row(70, 50, hit=True)] * 9 + [_row(70, 50)])
        assert _check(v, "retrieval_hit_rate").message == (
            "Retrieval hit rate = 100.0% (9/9; 1 unrecorded) (min 60%)"
        )

    def test_a_warn_only_verdict_renders_warnings_and_no_blocking_section(self):
        evals = [_row(5, 5)] * 7 + [_row(100, 5)] * 3
        verdict = evaluate_trust(evals)
        assert verdict.warnings and not verdict.failures
        md = format_trust_block(verdict)
        assert "**Warnings:**" in md
        assert "**Blocking issues:**" not in md
