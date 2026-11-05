# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Each input refusal that PR #393 converts to InputError is pinned by a test that reaches it.

A refusal is exit 2 with its message through `cli.main`. Each test runs the command through
`refusal()`, the same way, and fails if the refusal is removed (the command then exits 0 or 1).
"""

from __future__ import annotations

import json

import pytest

from dokimi.cli import build_parser
from tests.cli_refusal import refusal


def _arms(tmp_path, n=2, *, baseline_only=False):
    """An arms run: every question has a baseline answer, and a skill answer unless baseline_only."""
    def arms_of(i):
        arms = {"baseline": {"answer": f"b{i}", "metadata": {"arm_kind": "baseline"}}}
        if not baseline_only:
            arms["skill"] = {"answer": f"s{i}", "metadata": {"arm_kind": "skill"}}
        return arms

    data = {"library_name": "L", "baseline_arm": "baseline", "arms": ["baseline", "skill"],
            "answers": [{"question_id": f"Q{i}", "question_text": f"question {i}?", "arms": arms_of(i)}
                        for i in range(n)]}
    path = tmp_path / "arms.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run(argv, expect_code=2):
    args = build_parser().parse_args(argv)
    with refusal() as outcome:
        args.func(args)
    assert outcome.code == expect_code, outcome.err
    return outcome


@pytest.fixture
def no_judge(monkeypatch):
    """Judge construction is offline: the refusal fires before any call, but the command builds one."""
    from dokimi.eval import judge as judge_mod

    class FakeJudge:
        def __init__(self, **_):
            self.llm = None

    monkeypatch.setattr(judge_mod, "Judge", FakeJudge)


def test_pairwise_refuses_when_no_question_has_a_baseline_and_another_arm(tmp_path, no_judge):
    path = _arms(tmp_path, baseline_only=True)
    out = _run(["eval", "score", "pairwise", "--answers", str(path)])
    assert "no question has both" in out.err


def test_checklist_refuses_when_no_question_has_expected_topics(tmp_path, no_judge):
    path = _arms(tmp_path)
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps({"questions": [{"id": f"Q{i}", "question": "q"} for i in range(2)]}),
                         encoding="utf-8")
    out = _run(["eval", "score", "checklist", "--answers", str(path), "--questions", str(questions)])
    assert "no question in the answers file has expected_topics" in out.err


def test_wording_refuses_an_unknown_rewrite(tmp_path, no_judge):
    out = _run(["eval", "judge", "wording", "--answers", str(_arms(tmp_path)), "--wordings", "no-such-rewrite"])
    assert "unknown wording(s)" in out.err


def test_cross_judge_grounded_needs_a_questions_file(tmp_path, no_judge):
    out = _run(["eval", "judge", "agreement", "--answers", str(_arms(tmp_path)), "--grounded"])
    assert "--grounded needs --questions" in out.err


def test_cross_judge_refuses_an_unreadable_questions_file(tmp_path, no_judge):
    bad = tmp_path / "q.json"
    bad.write_text("{truncated", encoding="utf-8")
    out = _run(["eval", "judge", "agreement", "--answers", str(_arms(tmp_path)), "--grounded",
                "--questions", str(bad)])
    assert "cannot read questions" in out.err


def test_cross_judge_refuses_a_judge_spec_without_a_model(tmp_path, no_judge):
    out = _run(["eval", "judge", "agreement", "--answers", str(_arms(tmp_path)), "--judges", "amazon-bedrock"])
    assert "must be provider/model" in out.err


def test_judge_validity_refuses_an_unknown_degradation(tmp_path, no_judge):
    out = _run(["eval", "judge", "validate", "--answers", str(_arms(tmp_path)), "--variants", "no-such-variant"])
    assert "unknown degradation(s)" in out.err


def test_import_refuses_a_malformed_promptfoo_config(tmp_path):
    cfg = tmp_path / "promptfooconfig.yaml"
    cfg.write_text("prompts: ['{{q}}']\ntests:\n  - vars: {q: x}\n    threshold: true\n"
                   "    assert: [{type: contains, value: x}]\n", encoding="utf-8")
    out = _run(["eval", "import", str(cfg), "--format", "promptfoo", "-o", str(tmp_path / "q.json")])
    assert "threshold True is not a number" in out.err


def _questions(tmp_path, questions):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"questions": questions}), encoding="utf-8")
    return path


def _answers(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"answers": [{"question_id": "1", "question_text": "Q",
                                             "without_docs": {"answer": "yes"}}]}), encoding="utf-8")
    return path


def test_reference_refuses_questions_whose_grading_cannot_be_graded(tmp_path):
    questions = _questions(tmp_path, [{"id": "1", "question": "Q", "grading": {"checks": [{"type": "no-such-check"}]}}])
    out = _run(["eval", "score", "reference", "--answers", str(_answers(tmp_path)), "--questions", str(questions),
                "-o", str(tmp_path / "e.json"), "--no-judge"])
    assert "cannot be graded" in out.err


def test_reference_refuses_when_no_answered_question_has_a_grading_block(tmp_path):
    questions = _questions(tmp_path, [{"id": "1", "question": "Q"}])
    out = _run(["eval", "score", "reference", "--answers", str(_answers(tmp_path)), "--questions", str(questions),
                "-o", str(tmp_path / "e.json"), "--no-judge"])
    assert "no answered question has a grading block" in out.err


def test_native_import_refuses_a_log_that_is_neither_format(tmp_path):
    log = tmp_path / "run.log"
    log.write_text('{"unrelated": true}\n', encoding="utf-8")
    out = _run(["eval", "native-import", str(log), "--answers-out", str(tmp_path / "a.json")])
    assert "neither an Inspect json log nor a promptfoo output file" in out.err
