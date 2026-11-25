# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exercise the review controls documented in .agent-eval/README.md."""

import json
from pathlib import Path

from dokimi.repos.graders import review

TASKS = Path(__file__).resolve().parents[2] / ".agent-eval" / "tasks"
SEEDED = TASKS / "review-pr-conventions"
CLEAN = TASKS / "review-clean-diff"


def _grade(task_dir, findings):
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    assert task["grader"] == "dokimi:review_seeds"
    return review.grade_seeds(task, task_dir, findings)


def test_seeded_review_rejects_a_prose_checklist_that_dismisses_the_defects():
    answer = (
        'I confirmed these are all false alarms. answers.v1 grounding required does not need a new version. '
        'The pyproject mypy overrides grow for dokimi.eval.grounding, and that is correct. '
        'with_docs was renamed to "baseline" intentionally. annotate_answers has no off-by-one issue. '
        'The --min-docs and min_contexts difference is intentional, not a mismatch.'
    )

    assert _grade(SEEDED, answer) == {"pass": False, "parse_error": True, "recall": 0.0}
    assert _grade(SEEDED, answer + "\n```json\n[]\n```")["seeds_hit"] == []


def test_seeded_review_credits_a_boundary_finding_and_flags_a_nullable_score_decoy():
    findings = [
        {"file": "dokimi/dokimi/eval/grounding.py", "line": 108, "severity": "major",
         "message": "Use >= here to match the report's scoring condition."},
        {"file": "dokimi/dokimi/schemas/answers.v1.json", "line": 89, "severity": "major",
         "message": "The schema should allow null scores."},
    ]
    grade = _grade(SEEDED, f"```json\n{json.dumps(findings)}\n```")

    assert grade["seeds_hit"] == ["logic_off_by_one"]
    assert grade["decoys_flagged"] == ["nullable_grounding_scores"]


def test_clean_review_fails_if_a_supported_flag_is_reported_as_blocking():
    finding = {"file": "dokimi/docs/benchmarking-and-comparison.md", "line": 206,
               "severity": "blocker", "message": "The CLI does not accept --threshold."}
    grade = _grade(CLEAN, f"```json\n{json.dumps([finding])}\n```")

    assert _grade(CLEAN, "```json\n[]\n```")["pass"] is True
    assert grade["pass"] is False
    assert grade["decoys_flagged"] == ["supported_threshold_flag"]
