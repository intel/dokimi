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
    assert (s.without_min, s.without_max) == (40.0, 60.0)
    assert (s.delta_min, s.delta_max) == (0.0, 20.0)
    assert (s.improvements, s.degradations, s.neutral) == (1, 0, 1)


def test_paired_summary_delta_is_not_the_difference_of_the_rounded_means():
    from dokimi.eval.stats import paired_summary

    # 60.04 - 50.06 = 9.98 rounds to 10.0; the rounded means 60.0 and 50.1 would give 9.9.
    s = paired_summary([{"question_id": "q1", "question_text": "Q?",
                         "with_docs": {"aggregate": 60.04}, "without_docs": {"aggregate": 50.06}}])
    assert round(s.delta_mean, 1) == 10.0
    assert round(round(s.with_mean, 1) - round(s.without_mean, 1), 1) == 9.9


def test_paired_summary_of_nothing_is_all_zero():
    from dokimi.eval.stats import paired_summary

    s = paired_summary([])
    assert (s.total, s.paired, s.unpaired, s.with_mean, s.delta_mean) == (0, 0, 0, 0.0, 0.0)


# Pairs whose arm means are not exact at one decimal: (80.67, 70.33). Rounding each mean first
# gives 80.7 - 70.3 = +10.4; the delta of the unrounded means is +10.3, which both reports print.
INEXACT = [
    {"question_id": f"q{i}", "question_text": f"Question {i}?",
     "with_docs": {"aggregate": w}, "without_docs": {"aggregate": wo}, "delta": w - wo}
    for i, (w, wo) in enumerate([(80.0, 70.0), (81.0, 70.0), (81.0, 71.0)], 1)
]


def test_both_reports_take_the_delta_from_unrounded_means(tmp_path):
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"evaluations": INEXACT}), encoding="utf-8")
    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    eval_md = md.read_text(encoding="utf-8")
    assert _eval_report_all_row(eval_md) == {"with": 80.7, "without": 70.3, "delta": 10.3}
    assert "| **Total** | 3 | 80.7 | 70.3 | **+10.3** | 3 | 0 | 0 |" in eval_md

    generated = json.loads(ReportGenerator().generate_report({"evaluations": INEXACT}, {"questions": []}, "json"))["stats"]
    assert generated["delta"] == {"avg": 10.3, "min": 10.0, "max": 11.0}
    assert (generated["doc_improvements"], generated["doc_degradations"]) == (3, 0)
    # One group holds every question here, so its delta is the same unrounded-mean delta.
    assert json.loads(ReportGenerator().generate_report({"evaluations": INEXACT}, {"questions": []}, "json"))[
        "clusters"][0]["delta_avg"] == 10.3


# The grouped tables: q1 and q3 share a persona and a source type, so their group holds the
# unpaired q3; q2 is a group of its own.
QUESTIONS = [
    {"id": "q1", "persona_id": "pins", "source_type": "generated"},
    {"id": "q2", "persona_id": "pools", "source_type": "manual"},
    {"id": "q3", "persona_id": "pins", "source_type": "generated"},
]


def test_grouped_tables_average_each_group_over_its_pairs_and_count_the_unpaired():
    report = json.loads(ReportGenerator().generate_report(
        {"evaluations": EVALUATIONS}, {"questions": QUESTIONS}, "json"))
    clusters = {c["topic"]: c for c in report["clusters"]}
    assert clusters["pins"] == {"topic": "pins", "count": 2, "unpaired": 1,
                                "with_avg": 80.0, "without_avg": 60.0, "delta_avg": 20.0}
    assert clusters["pools"] == {"topic": "pools", "count": 1, "unpaired": 0,
                                 "with_avg": 40.0, "without_avg": 40.0, "delta_avg": 0.0}
    sources = {s["source_type"]: s for s in report["sources_summary"]}
    assert sources["generated"] == {"source_type": "generated", "count": 2, "unpaired": 1,
                                    "with_avg": 80.0, "without_avg": 60.0, "delta_avg": 20.0}
    assert sources["manual"]["with_avg"] == 40.0 and sources["manual"]["delta_avg"] == 0.0


def test_grouped_tables_print_the_count_and_unpaired_count_beside_their_means():
    gen_md = ReportGenerator().generate_report({"evaluations": EVALUATIONS}, {"questions": QUESTIONS}, "markdown")
    assert "| pins | 2 (1 unpaired) | 80.0 | 60.0 | **+20.0** |" in gen_md
    assert "| pools | 1 | 40.0 | 40.0 | **+0.0** |" in gen_md
    assert "| generated | 2 (1 unpaired) | 80.0 | 60.0 | **+20.0** |" in gen_md
    assert "| manual | 1 | 40.0 | 40.0 | **+0.0** |" in gen_md


# The eval report's groups: two golden questions (onetbb-Q prefix, one unpaired) and three
# dynamic ones (one unpaired), in two difficulties. Pairs: (80, 60), (40, 40), (60, 50).
GROUPED_EVALUATIONS = [
    {"question_id": "onetbb-Q001", "question_text": "How do I pin threads?", "difficulty": "easy",
     "with_docs": {"aggregate": 80.0}, "without_docs": {"aggregate": 60.0}, "delta": 20.0},
    {"question_id": "onetbb-Q002", "question_text": "How do I size a pool?", "difficulty": "easy",
     "with_docs": {"aggregate": 90.0}, "without_docs": None, "delta": None},
    {"question_id": "gen-1", "question_text": "How do I flush a queue?", "difficulty": "hard",
     "with_docs": {"aggregate": 40.0}, "without_docs": {"aggregate": 40.0}, "delta": 0.0},
    {"question_id": "gen-2", "question_text": "How do I cancel a task?", "difficulty": "hard",
     "with_docs": {"aggregate": 60.0}, "without_docs": {"aggregate": 50.0}, "delta": 10.0},
    {"question_id": "gen-3", "question_text": "How do I wait on a flag?", "difficulty": "hard",
     "with_docs": {"aggregate": 100.0}, "without_docs": None, "delta": None},
]


def test_eval_report_group_rows_count_their_unpaired_questions(tmp_path):
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"evaluations": GROUPED_EVALUATIONS}), encoding="utf-8")
    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    eval_md = md.read_text(encoding="utf-8")

    assert _eval_report_all_row(eval_md) == {"with": 60.0, "without": 50.0, "delta": 10.0}
    assert "| **All** | 5 (2 unpaired) |" in eval_md
    # Summary table: the dynamic and golden rows. Whole lines, because the breakdown rows below
    # carry the same cells with more columns after them.
    lines = eval_md.splitlines()
    assert "| Generated (dynamic) | 3 (1 unpaired) | 50.0 | 45.0 | **+5.0** |" in lines
    assert "| Golden (static) | 2 (1 unpaired) | 80.0 | 60.0 | **+20.0** |" in lines
    # Breakdown by type.
    assert "| Dynamic (generated) | 3 (1 unpaired) | 50.0 | 45.0 | **+5.0** |" in eval_md
    assert "| Golden (static) | 2 (1 unpaired) | 80.0 | 60.0 | **+20.0** |" in eval_md
    # Breakdown by difficulty.
    assert "| easy | 2 (1 unpaired) | 80.0 | 60.0 | **+20.0** |" in eval_md
    assert "| hard | 3 (1 unpaired) | 50.0 | 45.0 | **+5.0** |" in eval_md


def test_top_and_bottom_performers_rank_the_complete_pairs_like_the_overall_max():
    report = json.loads(ReportGenerator().generate_report({"evaluations": EVALUATIONS}, {"questions": []}, "json"))
    # q3 scored 100 in the context arm alone; it is not in any table that the Overall Max bounds.
    assert [(r["question_id"], r["score"]) for r in report["top_with_docs"]] == [("q1", 80.0), ("q2", 40.0)]
    assert [(r["question_id"], r["score"]) for r in report["bottom_with_docs"]] == [("q2", 40.0), ("q1", 80.0)]
    assert max(r["score"] for r in report["top_with_docs"]) == report["stats"]["with_docs"]["max"]

    gen_md = ReportGenerator().generate_report({"evaluations": EVALUATIONS}, {"questions": []}, "markdown")
    assert "| q3 |" not in gen_md
    assert "over the 2 complete pairs; 1 unpaired (scored in one arm only) left out" in gen_md


# Four complete pairs: two improved ((80, 60), (70, 50)), one degraded ((40, 50)), one neutral
# ((60, 60)). Two more questions are unpaired, one per arm. The win rate and the degradation rate
# are over the four pairs, the same set the Overall delta uses: 50% and 25%. Over all six
# questions they would be 33% and 17%.
WIN_RATE_EVALUATIONS = [
    {"question_id": "q1", "question_text": "How do I pin threads?",
     "with_docs": {"aggregate": 80.0}, "without_docs": {"aggregate": 60.0}, "delta": 20.0},
    {"question_id": "q2", "question_text": "How do I size a pool?",
     "with_docs": {"aggregate": 40.0}, "without_docs": {"aggregate": 50.0}, "delta": -10.0},
    {"question_id": "q3", "question_text": "How do I flush a queue?",
     "with_docs": {"aggregate": 60.0}, "without_docs": {"aggregate": 60.0}, "delta": 0.0},
    {"question_id": "q4", "question_text": "How do I cancel a task?",
     "with_docs": {"aggregate": 70.0}, "without_docs": {"aggregate": 50.0}, "delta": 20.0},
    {"question_id": "q5", "question_text": "How do I wait on a flag?",
     "with_docs": {"aggregate": 90.0}, "without_docs": None, "delta": None},
    {"question_id": "q6", "question_text": "How do I join a thread?",
     "with_docs": None, "without_docs": {"aggregate": 30.0}, "delta": None},
]


def test_eval_report_win_and_degradation_rates_divide_by_the_complete_pairs(tmp_path):
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"evaluations": WIN_RATE_EVALUATIONS}), encoding="utf-8")
    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    eval_md = md.read_text(encoding="utf-8")

    assert "- **Win rate:** 50% questions improved with context (2/4)." in eval_md
    assert "- **Degradations:** 1 questions (25%)." in eval_md


def test_report_eval_with_no_complete_pair_says_so_instead_of_a_zero_delta(tmp_path):
    # One question scored in each arm only: no pair, so there is no delta to report.
    evaluations = [
        {"question_id": "q1", "question_text": "How do I pin threads?",
         "with_docs": {"aggregate": 80.0}, "without_docs": None, "delta": None},
        {"question_id": "q2", "question_text": "How do I size a pool?",
         "with_docs": None, "without_docs": {"aggregate": 60.0}, "delta": None},
    ]
    path = tmp_path / "eval" / "lib.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"judge_model": "m", "evaluations": evaluations}), encoding="utf-8")
    md = tmp_path / "lib.md"
    generate_report(str(path), str(md), str(tmp_path / "lib.qa.json"))
    text = md.read_text(encoding="utf-8")

    all_row = next(line for line in text.splitlines() if line.startswith("| **All** |"))
    assert all_row.split("|")[3:6] == [" — ", " — ", " — "]
    assert "Overall delta: +0.0" not in text
    assert "no complete pairs" in text
