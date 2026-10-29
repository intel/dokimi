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
    assert result["status"] == "inconclusive"  # nothing was graded, so nothing was verified
    assert result["violations"] == []
    assert result["summary"]["unscored"] == 3  # wrong, empty and hedge; no canary, no stuffing
    assert result["summary"]["probed"] == 0


@pytest.mark.parametrize("questions", [
    [{"id": "1", "question": "Capital of France?"}],  # no grading block at all
    [_q("1", {"checks": [{"type": "judge", "criterion": "names the capital"}]})],
])
def test_a_run_that_probed_nothing_is_inconclusive_not_pass(questions):
    result = run(questions)
    assert result["status"] == "inconclusive"
    assert result["summary"]["probed"] == 0


def test_stuffing_is_not_an_attack_on_a_block_that_needs_every_value():
    # "Paris Lyon" satisfies a block that requires both values, so the grader is right to accept it.
    result = run([_q("1", _match("Paris", "Lyon", mode="all"))])
    assert result["status"] == "pass"
    assert "stuffed" not in result["questions"][0]["probes"]


def test_stuffing_is_not_an_attack_on_several_checks_that_each_accept_a_value():
    grading = {"checks": [{"type": "match", "values": ["Paris"]}, {"type": "match", "values": ["Lyon"]}]}
    assert redteam.attacks(grading)["stuffed"] is None


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


def test_command_exits_1_when_nothing_was_graded(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [{"id": "1", "question": "Capital of France?"}])
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json"))
    assert exc.value.code == 1


def test_command_exits_2_when_the_artifact_cannot_be_written(tmp_path, monkeypatch, capsys):
    _patch_questions(monkeypatch, [_q("1", _match("Paris"))])
    blocker = tmp_path / "a_file"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json", output=blocker / "out.json"))
    assert exc.value.code == 2  # not 1: CI must not read an unwritable -o as a blocked run
    assert capsys.readouterr().err.startswith("Error:")


def test_command_exits_2_on_an_unreadable_question_set(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "missing.json"))
    assert exc.value.code == 2
    assert capsys.readouterr().err.startswith("Error:")


def test_a_negated_check_is_not_blocked_by_the_answers_it_passes_by_design():
    # "must not say Paris": a wrong, empty or hedged answer lacks Paris and so passes the check.
    grading = {"checks": [{"type": "match", "values": ["Paris"], "negate": True}]}
    probes = redteam.probe(grading, "Capital of France?")
    assert "wrong" not in probes and "empty" not in probes and "hedge" not in probes
    assert "canary" not in probes


@pytest.mark.parametrize("value", ["one", "SETUP"])
def test_a_check_value_that_occurs_in_the_hedge_skips_the_hedge_probe(value):
    # The hedge sentence says "which one" and "the setup"; a grader that accepts the word would
    # be blamed for a hedge it was never asked to reject.
    grading = {"checks": [{"type": "fuzzy", "values": [value]}]}
    assert redteam.attacks(grading)["hedge"] is None
    assert redteam.attacks({"checks": [{"type": "match", "values": ["Paris"]}]})["hedge"] == redteam.HEDGE


def test_a_negated_check_still_fails_the_stuffed_answer_that_carries_its_value():
    grading = {"checks": [{"type": "match", "values": ["Paris", "Lyon"], "negate": True}]}
    assert redteam.attacks(grading)["stuffed"] == "Paris Lyon"
    probes = redteam.probe(grading, "Q?")
    assert probes["stuffed"].passed is False and not probes["stuffed"].violation


def test_a_negated_check_does_not_silence_the_attacks_on_the_positive_check():
    # regex ".*" accepts any answer, and "must not say Lyon" accepts the wrong one too: the block is
    # gameable, so the run is blocked rather than passing with nothing probed.
    grading = {"checks": [{"type": "regex", "values": [".*"]}, {"type": "match", "values": ["Lyon"], "negate": True}]}
    result = run([_q("1", grading)])
    assert result["status"] == "blocked"
    assert {v["attack"] for v in result["violations"]} == {"wrong", "empty", "hedge"}
    assert result["summary"]["probed"] == 1


def test_a_negated_check_beside_a_positive_check_leaves_the_positive_check_to_reject_the_attacks():
    grading = {"checks": [{"type": "match", "values": ["Paris"]}, {"type": "match", "values": ["Lyon"], "negate": True}]}
    result = run([_q("1", grading)])
    probes = result["questions"][0]["probes"]
    assert result["status"] == "pass"
    assert all(probes[name]["passed"] is False for name in ("wrong", "empty", "hedge"))


def test_a_block_of_only_negated_checks_runs_no_wrong_empty_or_hedge_probe_and_reports_it():
    grading = {"checks": [{"type": "match", "values": ["Paris"], "negate": True}]}
    result = run([_q("1", grading)])
    row = result["questions"][0]
    assert row["negated_only"] is True and result["summary"]["negated_only"] == 1
    assert not {"wrong", "empty", "hedge"} & set(row["probes"])
    assert result["status"] == "inconclusive"
    assert "1 questions with only negated checks: wrong, empty and hedge not run" in format_table(result)


@pytest.mark.parametrize("check", [
    {"type": "match", "values": ["several things depending on the setup"], "location": "any"},  # comma is punctuation
    {"type": "fuzzy", "values": ["the setup and I"]},  # articles drop out of the grader's normalised text
])
def test_a_value_the_grader_finds_in_the_hedge_after_normalising_skips_the_hedge_probe(check):
    assert redteam.attacks({"checks": [check]})["hedge"] is None
    result = run([_q("1", {"checks": [check]})])
    assert "hedge" not in {v["attack"] for v in result["violations"]}


def test_a_match_without_punctuation_stripping_gets_a_canary_the_grader_accepts():
    grading = {"checks": [{"type": "match", "values": ["Paris"], "ignore_punctuation": False}]}
    assert canary_for(grading) == "The answer is Paris"  # a full stop would end the answer, not the value
    result = run([_q("1", grading)])
    assert result["status"] == "pass" and result["questions"][0]["canary"] is True
