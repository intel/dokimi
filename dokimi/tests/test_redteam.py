# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``dokimi.eval.redteam`` and ``eval redteam``: attacks against grading blocks.

Gradings are built in memory; judge checks are never sent to a model (no ``ask`` is passed).
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

from dokimi.commands.redteam import cmd_eval_redteam
from dokimi.eval import redteam
from dokimi.eval.redteam import canary_for, format_table, run
from dokimi.eval.reference_checks import CheckResult, Grade


def _q(qid, grading, text="What is the capital of France?"):
    return {"id": qid, "question": text, "grading": grading}


def _match(*values, mode="any"):
    return {"checks": [{"type": "match", "values": list(values), "mode": mode}], "threshold": None}


def test_a_grader_that_rejects_every_attack_passes_the_red_team():
    result = run([_q("1", _match("Paris"))])
    assert result["status"] == "pass" and result["violations"] == []
    assert set(result["questions"][0]["probes"]) == {"canary", "wrong", "empty", "hedge"}
    assert result["attacks"]["canary"] == {"expected": "pass", "scored": 1, "accepted": 1, "rate": 1.0}


def test_keyword_stuffing_that_the_grader_accepts_blocks_the_run():
    # Two alternative values under "any": the bare list ends with one of them, so the match accepts it.
    result = run([_q("1", _match("Paris", "Lyon"))])
    assert result["status"] == "blocked"
    assert result["violations"] == [{"question_id": "1", "attack": "stuffed", "passed": True}]


def test_a_grader_that_accepts_anything_is_blocked_on_every_attack():
    grading = {"checks": [{"type": "regex", "values": [".*"]}]}
    result = run([_q("1", grading)])
    attacks = {v["attack"] for v in result["violations"]}
    assert result["status"] == "blocked" and attacks == {"wrong", "empty", "hedge"}
    assert result["questions"][0]["canary"] is False


def test_a_rejected_canary_is_a_violation(monkeypatch):
    def reject_all(grading, question, answer, ask=None):
        return Grade(checks=[CheckResult("match", False, 0.0)])

    monkeypatch.setattr(redteam, "grade", reject_all)
    result = run([_q("1", _match("Paris"))])
    assert result["violations"] == [{"question_id": "1", "attack": "canary", "passed": False}]
    assert result["status"] == "blocked"


def test_a_judge_check_without_a_judge_is_unscored_and_does_not_block():
    grading = {"checks": [{"type": "judge", "criterion": "names the capital"}]}
    result = run([_q("1", grading)])
    assert result["status"] == "pass"
    assert result["summary"]["unscored"] == 3  # wrong, empty and hedge; no canary, no stuffing
    assert result["summary"]["probed"] == 0


@pytest.mark.parametrize("grading", [
    {"checks": [{"type": "match", "values": ["Paris", "Lyon"], "mode": "all"}]},
    {"checks": [{"type": "match", "values": ["Paris"], "negate": True}]},
    {"checks": [{"type": "regex", "values": ["Paris"]}]},
    {"checks": [{"type": "match", "values": ["Paris"]}, {"type": "match", "values": ["Lyon"]}]},
])
def test_no_canary_when_the_block_is_not_one_literal_check(grading):
    assert canary_for(grading) is None


def test_canary_text_follows_the_check_kind():
    assert canary_for(_match("Paris")) == "The answer is Paris."
    assert canary_for({"checks": [{"type": "match", "values": ["Paris"], "location": "begin"}]}) == "Paris is the answer."
    assert canary_for({"checks": [{"type": "exact", "value": "Paris"}]}) == "Paris"
    assert canary_for({"checks": [{"type": "choice", "values": ["b", "a"]}]}) == "ANSWER: A, B"
    assert canary_for({"checks": [{"type": "json", "value": 3}]}) == "3"


def test_a_block_that_cannot_be_graded_is_refused():
    with pytest.raises(ValueError, match="cannot be graded"):
        run([_q("1", {"checks": [{"type": "nope"}]})])


def test_table_names_each_violation_and_the_status():
    result = run([_q("7", _match("Paris", "Lyon"))])
    table = format_table(result)
    assert "violation: question 7: stuffed answer accepted" in table
    assert "status blocked: 1 questions" in table


def _args(path, **overrides):
    base = {"questions": str(path), "judge": False, "judge_model": "m", "judge_provider": "anthropic",
            "output": None}
    return argparse.Namespace(**{**base, **overrides})


def _patch_questions(monkeypatch, questions):
    monkeypatch.setattr("dokimi.questions.artifact.load_questions",
                        lambda path: SimpleNamespace(artifact={"questions": questions}))


def test_command_passes_and_writes_a_redteam_artifact(tmp_path, monkeypatch, capsys):
    _patch_questions(monkeypatch, [_q("1", _match("Paris"))])
    out = tmp_path / "redteam.json"
    cmd_eval_redteam(_args(tmp_path / "questions.json", output=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "redteam.v1" and saved["status"] == "pass"
    assert saved["questions_path"].endswith("questions.json")
    assert f"Wrote redteam artifact to {out}" in capsys.readouterr().out


def test_command_exits_1_when_the_run_is_blocked(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [_q("1", _match("Paris", "Lyon"))])
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json"))
    assert exc.value.code == 1


def test_command_exits_2_on_an_unreadable_question_set(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "missing.json"))
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("Error:")
