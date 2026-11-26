# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Exercise the review controls documented in .agent-eval/README.md."""

import json
from pathlib import Path

import pytest

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
    fence = "\x60" * 3
    minor = {"file": "dokimi/docs/benchmarking-and-comparison.md", "line": 206,
             "severity": "minor", "message": "The heading could be clearer."}
    assert _grade(CLEAN, f"{fence}json\n{json.dumps([minor])}\n{fence}")["pass"] is True


def _seeded(file, line, message):
    finding = {"file": file, "line": line, "severity": "major", "message": message}
    return _grade(SEEDED, "\x60\x60\x60json\n" + json.dumps([finding]) + "\n\x60\x60\x60")


@pytest.mark.parametrize(
    ("message", "hits"),
    [
        ("lift compares the with_docs answer to the baseline answer; document the sign", []),
        ("Rename the helper that reads `with_docs`", []),
        ("Replace the documentation link for `with_docs`. The baseline is correct.", []),
        ("Use `baseline` rather than `without_docs` in the report heading", []),
        ("Replace `without_docs` with `baseline` in the report heading", []),
        ("`baseline` replaces `without_docs` in the report heading", []),
        ("with_docs is renamed to baseline, so stored keys change", ["key_rename"]),
        ("The persisted key `with_docs` is now `with_context`", ["key_rename"]),
        ("The writer renamed the persisted with_docs key to with_context", ["key_rename"]),
        ("`with_context` and `baseline` replace `with_docs` and `without_docs`", ["key_rename"]),
        ("The persisted keys `with_docs` and `without_docs` have been renamed to `with_context` and `baseline`",
         ["key_rename"]),
    ],
)
def test_key_rename_needs_the_rename_not_just_both_key_names(message, hits):
    grade = _seeded("dokimi/dokimi/eval/grounding.py", 40, message)
    assert grade["seeds_hit"] == hits


@pytest.mark.parametrize("message", [
    "len(contexts) >= min_contexts is correct as written; the boundary is inclusive.",
    "The boundary correctly includes answers with exactly min_contexts contexts.",
    "No off-by-one issue at the context threshold.",
    "Continue to use `>=`; the comparison is already correct.",
])
def test_seeded_boundary_rejects_findings_that_confirm_the_comparison(message):
    grade = _seeded("dokimi/dokimi/eval/grounding.py", 110, message)
    assert grade["seeds_hit"] == []



@pytest.mark.parametrize("message", [
    "Answers with exactly `min_contexts` contexts are skipped.",
    "Replace `>` with `>=` so the minimum is included.",
    "Use `>=` here to match the scoring condition.",
])
def test_seeded_boundary_credits_multiple_defect_wordings(message):
    grade = _seeded("dokimi/dokimi/eval/grounding.py", 110, message)
    assert grade["seeds_hit"] == ["logic_off_by_one"]


def test_seed_keywords_accept_common_wordings():
    assert _seeded("dokimi/dokimi/eval/grounding.py", 40,
                   "len(contexts) > min_contexts should be >=")["seeds_hit"] == ["logic_off_by_one"]
    schema = _seeded("dokimi/dokimi/schemas/answers.v1.json", 85,
                     "narrows answers.v1 in place; add answers.v2 or keep it optional (nullable is fine)")
    assert schema["seeds_hit"] == ["schema_in_place"]
    assert schema["decoys_flagged"] == []
    grounding = _seeded("dokimi/dokimi/schemas/answers.v1.json", 37,
                        "Making grounding required in answers.v1 rejects older answers.")
    assert grounding["seeds_hit"] == ["schema_in_place"]


def test_a_nullable_score_complaint_that_says_narrow_is_the_decoy_not_the_schema_seed():
    grade = _seeded("dokimi/dokimi/schemas/answers.v1.json", 89,
                    "The grounding score types are too narrow; make score, baseline_score and lift nullable.")
    assert grade["seeds_hit"] == []
    assert grade["decoys_flagged"] == ["nullable_grounding_scores"]


@pytest.mark.parametrize("message", [
    "The score types are too narrow. Make score, baseline_score and lift nullable in place.",
    "Make score, baseline_score and lift nullable by updating `answers.v1` in place.",
])
def test_a_nullable_decoy_with_in_place_wording_is_not_a_version_change(message):
    grade = _seeded("dokimi/dokimi/schemas/answers.v1.json", 89, message)
    assert grade["seeds_hit"] == []
    assert grade["decoys_flagged"] == ["nullable_grounding_scores"]
