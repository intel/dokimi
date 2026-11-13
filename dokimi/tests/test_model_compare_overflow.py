# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``extract_scores`` refuses a score difference beyond the float range instead of carrying inf (#639)."""

from __future__ import annotations

import pytest

from dokimi.report.model_compare import extract_scores
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


def test_extract_scores_keeps_a_finite_difference_near_the_range():
    (row,) = extract_scores(_run(1.0e308, 1.7e308))
    assert row["delta"] == 1.7e308 - 1.0e308
