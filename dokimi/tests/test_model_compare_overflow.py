# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``extract_scores`` refuses a score difference beyond the float range instead of carrying inf (#639)."""

from __future__ import annotations

import pytest

from dokimi.report.model_compare import extract_scores, generate_section_report
from dokimi.utils import InputError


def _run(baseline: float, treatment: float) -> dict:
    return {"baseline_arm": "baseline", "model": "m", "provider": "p", "evaluations": [
        {"question_id": "Q1", "scores": {"baseline": {"aggregate": baseline}, "arm": {"aggregate": treatment}}}]}


def test_extract_scores_refuses_scores_whose_difference_leaves_the_range():
    with pytest.raises(InputError, match="float range"):
        extract_scores(_run(-1.7e308, 1.7e308))


def test_extract_scores_refuses_it_in_the_answer_records_too():
    run = {"baseline_arm": "baseline", "answers": [{"question_id": "Q1", "arms": {
        "baseline": {"answer": "a", "evaluation": {"aggregate_score": -1.7e308}},
        "arm": {"answer": "a", "evaluation": {"aggregate_score": 1.7e308}}}}]}
    with pytest.raises(InputError, match="float range"):
        extract_scores(run)


def test_extract_scores_names_the_question_of_the_bad_scores():
    with pytest.raises(InputError, match=r"^question 'Q1': the difference of -1.7e\+308 and 1.7e\+308 leaves"):
        extract_scores(_run(1.7e308, -1.7e308))


def test_extract_scores_names_the_question_of_the_bad_scores_in_the_answer_records():
    run = {"baseline_arm": "baseline", "answers": [{"question_id": "Q7", "arms": {
        "baseline": {"answer": "a", "evaluation": {"aggregate_score": -1.7e308}},
        "arm": {"answer": "a", "evaluation": {"aggregate_score": 1.7e308}}}}]}
    with pytest.raises(InputError, match=r"^question 'Q7': "):
        extract_scores(run)


def test_extract_scores_refuses_integer_scores_whose_exact_difference_leaves_the_range():
    with pytest.raises(InputError, match=r"question 'Q1': the difference of an integer of 309 digits"):
        extract_scores(_run(-(10 ** 308), 10 ** 308))


def test_a_section_refuses_finite_deltas_whose_sum_leaves_the_range_naming_the_run():
    run = _run(0.0, 1e308)
    run["evaluations"].append({"question_id": "Q2", "scores": {"baseline": {"aggregate": 0.0},
                                                                 "arm": {"aggregate": 1e308}}})
    with pytest.raises(InputError, match=r"run 'r1': the sum of the magnitudes"):
        generate_section_report([("r1", run)], ["r1"], "Regular")


def test_a_section_names_the_run_and_the_question_of_a_difference_beyond_the_range():
    with pytest.raises(InputError, match=r"^run 'r1', question 'Q1': the difference of"):
        generate_section_report([("r1", _run(-1.7e308, 1.7e308))], ["r1"], "Regular")


def test_extract_scores_keeps_a_finite_difference_near_the_range():
    (row,) = extract_scores(_run(1.0e308, 1.7e308))
    assert row["delta"] == 1.7e308 - 1.0e308
