# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`report eval` and `report generate` must agree on the arm means and the delta of one eval file.

The fixture has two complete pairs and one question scored in the context arm only.
Both reports average the arms over the complete pairs, so both print the same numbers.
"""

import json

from dokimi.report.eval_report import generate_report
from dokimi.report.generator import ReportGenerator

# q1 and q2 are complete pairs; q3 has a context score and no baseline score (unpaired).
EVALUATIONS = [
    {"question_id": "q1", "question_text": "How do I pin threads?",
     "with_docs": {"aggregate": 80.0}, "without_docs": {"aggregate": 60.0}, "delta": 20.0},
    {"question_id": "q2", "question_text": "How do I size a pool?",
     "with_docs": {"aggregate": 40.0}, "without_docs": {"aggregate": 40.0}, "delta": 0.0},
    {"question_id": "q3", "question_text": "How do I flush a queue?",
     "with_docs": {"aggregate": 100.0}, "without_docs": None, "delta": None},
]


def _eval_report_all_row(markdown: str) -> dict:
    """The **All** row of the eval report's Summary table: context, baseline, delta."""
    row = next(line for line in markdown.splitlines() if line.startswith("| **All** |"))
    cells = [c.strip() for c in row.strip("|").split("|")]
    return {"with": float(cells[2]), "without": float(cells[3]), "delta": float(cells[4].strip("*+"))}


def test_report_eval_and_report_generate_print_the_same_arm_means_and_delta(tmp_path):
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"judge_model": "m", "evaluations": EVALUATIONS}), encoding="utf-8")

    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    evaluated = _eval_report_all_row(md.read_text(encoding="utf-8"))

    data = json.loads(path.read_text(encoding="utf-8"))
    generated = json.loads(ReportGenerator().generate_report(data, {"questions": []}, "json"))["stats"]
    generated_row = {
        "with": generated["with_docs"]["avg"],
        "without": generated["without_docs"]["avg"],
        "delta": generated["delta"]["avg"],
    }

    # Over the complete pairs only: context (80, 40) -> 60, baseline (60, 40) -> 50, delta 10.
    assert evaluated == {"with": 60.0, "without": 50.0, "delta": 10.0}
    assert generated_row == evaluated
    # The unpaired question is counted, not dropped silently.
    assert generated["paired_questions"] == 2
    assert generated["unpaired_questions"] == 1


def test_both_reports_say_which_questions_the_means_leave_out(tmp_path):
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"evaluations": EVALUATIONS}), encoding="utf-8")
    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    eval_md = md.read_text(encoding="utf-8")
    assert "| **All** | 3 (1 unpaired) |" in eval_md
    assert "over the 2 complete pairs; 1 unpaired (scored in one arm only) left out" in eval_md

    data = {"evaluations": EVALUATIONS}
    gen_md = ReportGenerator().generate_report(data, {"questions": []}, "markdown")
    assert "over the 2 complete pairs; 1 unpaired (scored in one arm only) left out" in gen_md


def test_paired_summary_counts_every_record_and_averages_only_the_pairs():
    from dokimi.eval.stats import paired_summary

    s = paired_summary(EVALUATIONS)
    assert (s.total, s.paired, s.unpaired) == (3, 2, 1)
    assert (s.with_mean, s.without_mean, s.delta_mean) == (60.0, 50.0, 10.0)
    assert (s.with_min, s.with_max) == (40.0, 80.0)  # the 100 of q3 is not in it
    assert (s.delta_min, s.delta_max) == (0.0, 20.0)
    assert (s.improvements, s.degradations, s.neutral) == (1, 0, 1)


def test_paired_summary_of_nothing_is_all_zero():
    from dokimi.eval.stats import paired_summary

    s = paired_summary([])
    assert (s.total, s.paired, s.unpaired, s.with_mean, s.delta_mean) == (0, 0, 0, 0.0, 0.0)
