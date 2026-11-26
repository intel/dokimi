# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Offline controls with answers checked against arithmetic and shell behavior."""

import pytest

from dokimi.eval.judge_validity import evaluate_pairs


@pytest.mark.parametrize(("correct_score", "incorrect_score", "accuracy", "passed"), [
    (100.0, 0.0, 1.0, True),
    (0.0, 100.0, 0.0, False),
])
def test_validity_distinguishes_correctness_from_length(
    correct_score, incorrect_score, accuracy, passed,
):
    arithmetic = "What does len([4, 7, 9]) return?"
    shell = "Which shell command prints the current working directory?"
    pairs = [
        {"base_id": "length", "library": "Python", "question": arithmetic,
         "variant": "wrong_numbers", "intact": "3", "degraded": "It returns 2."},
        {"base_id": "directory", "library": "Shell", "question": shell,
         "variant": "mismatched_answer", "intact": "pwd", "degraded": "ls"},
        {"base_id": "length", "library": "Python", "question": arithmetic,
         "variant": "padding", "intact": "3", "degraded": "3. The list has three items."},
    ]
    accepted = {
        arithmetic: {"3", "3. The list has three items."},
        shell: {"pwd"},
    }

    def score(_library, question, answer):
        return correct_score if answer in accepted[question] else incorrect_score

    report = evaluate_pairs(score, pairs, judge="reference")
    summary = report.summary()

    assert (summary["n_pairs"], summary["n_neutral_pairs"], summary["n_errors"]) == (2, 1, 0)
    assert summary["accuracy"] == accuracy
    assert summary["length_accuracy"] == 0.5
    assert summary["passed"] is passed
    assert report.rows[2].unmoved == 1.0
