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
import regex

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


def _accept_stuffed(monkeypatch):
    """A grader that accepts the stuffed answer and rejects every other attack."""
    real = redteam.grade

    def grade(grading, question, answer, ask=None):
        if answer == redteam.attacks(grading)["stuffed"]:
            return Grade(checks=[CheckResult("match", True, 1.0)])
        return real(grading, question, answer, ask)

    monkeypatch.setattr(redteam, "grade", grade)


def test_keyword_stuffing_that_the_grader_accepts_blocks_the_run(monkeypatch):
    _accept_stuffed(monkeypatch)
    result = run([_q("1", _match("Paris", "Lyon"))])
    assert result["status"] == "blocked"
    assert result["violations"] == [{"question_id": "1", "attack": "stuffed", "passed": True}]


@pytest.mark.parametrize("check", [
    {"type": "match", "values": ["4", "four"], "location": "any"},
    {"type": "match", "values": ["Paris", "Lyon"]},  # location end
    {"type": "match", "values": ["Paris", "Lyon"], "location": "begin"},
    {"type": "fuzzy", "values": ["Barack Obama", "Obama"]},
])
def test_a_correct_grader_with_aliases_is_not_blocked_by_the_stuffed_answer(check):
    # The stuffed answer is built so that a correct grader rejects it (values between decoys) or it is
    # not built at all (fuzzy, numeric, location any): a correct grader is never blocked.
    result = run([_q("1", {"checks": [check]})])
    assert result["status"] == "pass" and result["violations"] == []


def test_the_stuffed_answer_buries_the_aliases_between_decoys_for_an_end_or_begin_check():
    stuffed = redteam.attacks(_match("Paris", "Lyon"))["stuffed"]
    middle = stuffed.split()
    assert middle[1:3] == ["Paris", "Lyon"] and len(middle) == 4
    assert not stuffed.endswith(("Paris", "Lyon")) and not stuffed.startswith(("Paris", "Lyon"))
    assert redteam.attacks({"checks": [{"type": "match", "values": ["a", "b"], "location": "any"}]})["stuffed"] is None


def test_a_json_contains_check_with_list_values_is_stuffed_after_a_decoy_the_grader_rejects():
    # contains reads the first JSON in the text: the bare list "[1, 2] [3, 4]" starts with the value [1, 2],
    # which a correct grader accepts, so the list goes after a JSON decoy that is not one of the values.
    grading = {"checks": [{"type": "json", "values": [[1, 2], [3, 4]], "contains": True}]}
    stuffed = redteam.attacks(grading)["stuffed"]
    assert stuffed is not None and stuffed.startswith('{"planted"')
    assert redteam.grade(grading, "", stuffed).checks[0].passed is False
    result = run([_q("1", grading)])
    assert result["status"] == "pass" and result["violations"] == []


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


def test_a_judge_check_without_a_judge_with_a_negated_check_is_unscored_not_blocked():
    # The negated check passes wrong, empty and hedge by design; the judge check was never run, so
    # those passes say nothing about the block and must not count as violations.
    grading = {"checks": [{"type": "judge", "criterion": "names the capital"},
                          {"type": "match", "values": ["Lyon"], "negate": True}]}
    result = run([_q("1", grading)])
    assert result["status"] == "inconclusive"
    assert result["violations"] == []
    assert result["summary"]["probed"] == 0


def test_a_judge_check_without_a_judge_under_a_threshold_is_not_a_pass():
    # With a threshold the score is taken over the counted checks alone, so a missing judge can
    # turn a block that rejects nothing into one that passes the canary and rejects the attacks.
    grading = {"threshold": 0.5, "checks": [{"type": "judge", "criterion": "names Paris"},
                                            {"type": "match", "values": ["Paris"]}]}
    result = run([_q("1", grading)])
    assert result["status"] == "inconclusive"
    assert result["summary"]["probed"] == 0


SLOW = "slow pattern"  # the regex that runs past its timeout; any other pattern runs as written


@pytest.fixture
def regex_past_its_timeout(monkeypatch):
    """Make the regex check with the SLOW pattern run past its timeout, as a catastrophic one does."""
    real_search = regex.search

    def search(pattern, string, *args, **kwargs):
        if pattern == SLOW:
            raise TimeoutError("regex ran past its limit")
        return real_search(pattern, string, *args, **kwargs)

    monkeypatch.setattr(regex, "search", search)


@pytest.mark.parametrize("grading", [
    # No threshold: the negated match passes wrong, empty and hedge by design, and the regex never ran.
    {"checks": [{"type": "regex", "values": [SLOW]}, {"type": "match", "values": ["Lyon"], "negate": True}]},
    # Threshold: the score over the counted match alone would pass the canary and reject the attacks.
    {"threshold": 0.5, "checks": [{"type": "regex", "values": [SLOW]}, {"type": "match", "values": ["Paris"]}]},
])
def test_a_regex_past_its_timeout_leaves_the_probe_unscored_not_passed(regex_past_its_timeout, grading):
    result = run([_q("1", grading)])
    assert result["status"] == "inconclusive"
    assert result["violations"] == []
    assert result["summary"]["probed"] == 0


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


def test_a_stuffed_answer_never_ends_in_a_decoy_the_check_accepts():
    # QZX is a value, so a stuffed answer that ends in it is accepted by a correct grader; the decoy
    # is dropped and the answer ends in one the check rejects.
    grading = _match("Lyon", "QZX-BOGUS-REPLY-4096")
    stuffed = redteam.attacks(grading)["stuffed"]
    assert stuffed is not None and not stuffed.endswith("QZX-BOGUS-REPLY-4096")
    result = run([_q("1", grading)])
    assert result["violations"] == []
    assert result["status"] != "blocked"


def test_a_stuffed_answer_needs_two_decoys_the_check_rejects():
    grading = _match("PLANTED-WRONG-ANSWER-7731", "QZX-BOGUS-REPLY-4096", "ZKV-MISMATCH-2508")
    assert redteam.attacks(grading)["stuffed"] is None


def test_a_question_with_one_scored_probe_is_measured_when_another_probe_is_not():
    # Unmeasured means no probe scored; one unscored probe next to a scored one is not a reason
    # to call the question unmeasured.
    row = {"negated_only": False, "probes": {"canary": {"passed": None}, "wrong": {"passed": False}}}
    assert redteam._unmeasured(row) == ""


def test_canary_text_follows_the_check_kind():
    assert canary_for(_match("Paris")) == "The answer is Paris."
    assert canary_for({"checks": [{"type": "match", "values": ["Paris"], "location": "begin"}]}) == "Paris is the answer."
    assert canary_for({"checks": [{"type": "exact", "value": "Paris"}]}) == "Paris"
    assert canary_for({"checks": [{"type": "choice", "values": ["b", "a"]}]}) == "ANSWER: A, B"
    assert canary_for({"checks": [{"type": "json", "value": 3}]}) == "3"


def test_a_block_that_cannot_be_graded_is_refused():
    with pytest.raises(ValueError, match="cannot be graded"):
        run([_q("1", {"checks": [{"type": "nope"}]})])


def test_table_names_each_violation_and_the_status(monkeypatch):
    _accept_stuffed(monkeypatch)
    result = run([_q("7", _match("Paris", "Lyon"))])
    table = format_table(result)
    assert "violation: question 7: stuffed answer accepted" in table
    assert "status blocked: 1 questions" in table


def _args(path, **overrides):
    base = {"questions": str(path), "judge": None, "out": None}
    return argparse.Namespace(**{**base, **overrides})


def _patch_questions(monkeypatch, questions):
    monkeypatch.setattr("dokimi.questions.artifact.load_questions",
                        lambda path: SimpleNamespace(artifact={"questions": questions}))


def test_command_passes_and_writes_a_redteam_artifact(tmp_path, monkeypatch, capsys):
    _patch_questions(monkeypatch, [_q("1", _match("Paris"))])
    out = tmp_path / "redteam.json"
    cmd_eval_redteam(_args(tmp_path / "questions.json", out=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["schema_version"] == "redteam.v1" and saved["status"] == "pass"
    assert saved["questions_path"].endswith("questions.json")
    assert f"Wrote redteam artifact to {out}" in capsys.readouterr().out


def test_command_exits_1_when_the_run_is_blocked(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [_q("1", {"checks": [{"type": "regex", "values": [".*"]}]})])
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
        cmd_eval_redteam(_args(tmp_path / "questions.json", out=blocker / "out.json"))
    assert exc.value.code == 2  # not 1: CI must not read an unwritable --out as a blocked run
    assert capsys.readouterr().err.startswith("Error:")


def test_command_exits_2_on_a_question_set_nested_too_deep_to_read(tmp_path, capsys):
    path = tmp_path / "deep.json"
    path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(path))
    assert exc.value.code == 2  # not a traceback and exit 1: CI reads exit 1 as a blocked run
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
    assert set(row["probes"]) == {"stuffed"}  # the forbidden value itself, which must fail
    assert result["status"] == "inconclusive"  # the real grader accepts any wrong answer: nothing was verified
    assert result["summary"]["probed"] == 0 and result["summary"]["unprobed"] == 1
    assert "1 questions with only negated checks: wrong, empty and hedge not run" in format_table(result)


def test_a_negated_single_value_is_stuffed_with_that_value_and_must_fail():
    grading = {"checks": [{"type": "match", "values": ["Paris"], "negate": True}]}
    assert redteam.attacks(grading)["stuffed"] == "Paris"
    probes = redteam.probe(grading, "Capital of France?")
    assert set(probes) == {"stuffed"} and probes["stuffed"].passed is False and not probes["stuffed"].violation


@pytest.mark.parametrize("check", [
    {"type": "exact", "values": ["Paris", "Lyon"], "negate": True},
    {"type": "match", "values": ["Paris", "Lyon"], "location": "exact", "negate": True},
    {"type": "json", "values": [1, 2], "negate": True},
])
def test_a_negated_whole_answer_check_is_not_stuffed(check):
    # "Paris Lyon" is neither "Paris" nor "Lyon", so the negated check passes it: that is not an attack.
    grading = {"checks": [check]}
    assert redteam.attacks(grading)["stuffed"] is None
    result = run([_q("1", grading)])
    assert result["violations"] == [] and "stuffed" not in result["questions"][0]["probes"]


@pytest.mark.parametrize("check", [
    {"type": "match", "values": ["Paris", "Lyon"], "mode": "all", "negate": True},  # location end
    {"type": "match", "values": ["Paris", "Lyon"], "mode": "all", "location": "begin", "negate": True},
])
def test_a_negated_all_mode_check_is_not_stuffed(check):
    # "Paris Lyon" ends with only one value, so the "all" match is false and the negated check passes
    # it: a correct grader would be blamed for accepting it. The positive rule skips mode all alike.
    grading = {"checks": [check]}
    assert redteam.attacks(grading)["stuffed"] is None
    result = run([_q("1", grading)])
    assert result["violations"] == [] and "stuffed" not in result["questions"][0]["probes"]


@pytest.mark.parametrize("check", [
    {"type": "match", "values": ["31"]},  # the planted answer ends in 7731
    {"type": "match", "values": ["1"]},  # the planted answer ends in 1
    {"type": "fuzzy", "values": ["on"]},  # the planted answer contains "on" in "wrong"
])
def test_a_check_value_in_the_planted_wrong_answer_does_not_block_a_grader_that_rejects_it(check):
    result = run([_q("1", {"checks": [check]})])
    assert result["status"] == "pass" and result["violations"] == []
    assert result["questions"][0]["probes"]["wrong"]["passed"] is False  # still probed, on a clean wrong answer


def test_the_planted_wrong_answer_carries_none_of_the_positive_check_values():
    grading = {"checks": [{"type": "match", "values": ["1", "on"]}]}
    wrong = redteam.attacks(grading)["wrong"]
    assert wrong is not None and "1" not in wrong and "on" not in wrong.lower()


def test_the_wrong_attack_is_skipped_when_every_candidate_carries_a_check_value(monkeypatch):
    monkeypatch.setattr(redteam, "WRONG_CANDIDATES", ("PLANTED-WRONG-ANSWER-7731",))
    assert redteam.attacks({"checks": [{"type": "match", "values": ["31"]}]})["wrong"] is None


def test_the_summary_counts_the_questions_that_were_not_probed():
    questions = [_q("1", _match("Paris")), _q("2", {"checks": [{"type": "judge", "criterion": "names the capital"}]})]
    result = run(questions)
    assert result["summary"]["probed"] == 1 and result["summary"]["unprobed"] == 1
    assert result["status"] == "inconclusive"
    assert "1 unprobed" in format_table(result)


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


def test_a_numeric_check_does_not_block_on_a_planted_answer_it_reads_as_its_number():
    # The planted answer ends in 7731 and a numeric match reads that number: a clean candidate is chosen by
    # asking the check itself, not by looking for the value's text in it.
    check = {"type": "match", "values": ["-7731.0"], "numeric": True}
    wrong = redteam.attacks({"checks": [check]})["wrong"]
    assert wrong is not None and "7731" not in wrong
    result = run([_q("1", {"checks": [check]})])
    assert result["status"] == "pass" and result["violations"] == []


def test_a_short_value_the_check_does_not_find_in_the_hedge_keeps_the_hedge_probe():
    # "one" occurs in "which one applies", but an end match reads the last words of the hedge only.
    grading = {"checks": [{"type": "match", "values": ["one"]}]}
    assert redteam.attacks(grading)["hedge"] == redteam.HEDGE
    result = run([_q("1", grading)])
    assert result["questions"][0]["probes"]["hedge"]["passed"] is False


def test_a_threshold_block_that_a_negated_check_lifts_over_the_line_is_blocked():
    # Half the score comes from "must not say London", which every wrong, empty or hedged answer earns:
    # the real grader passes them at 0.5, so the block is gameable.
    grading = {"threshold": 0.5, "checks": [{"type": "match", "values": ["Paris"]},
                                            {"type": "match", "values": ["London"], "negate": True}]}
    result = run([_q("1", grading)])
    assert result["status"] == "blocked"
    assert {v["attack"] for v in result["violations"]} == {"wrong", "empty", "hedge"}


def test_one_probed_question_does_not_make_a_set_pass_when_the_others_were_not_measured():
    judge = {"checks": [{"type": "judge", "criterion": "names the capital"}]}
    questions = [_q(str(i), judge) for i in range(5)] + [_q("m", _match("Paris"))]
    result = run(questions)
    assert result["status"] == "inconclusive" and result["violations"] == []
    assert result["summary"]["probed"] == 1 and result["summary"]["unprobed"] == 5
    table = format_table(result)
    assert "not measured: question 0 (no probe scored)" in table
    assert "question m" not in table.split("not measured:")[1]


def test_a_question_with_only_negated_checks_is_named_in_the_table_as_not_measured():
    only_negated = {"checks": [{"type": "match", "values": ["error"], "negate": True}]}
    result = run([_q("g", only_negated), _q("m", _match("Paris"))])
    assert result["status"] == "inconclusive"
    assert "not measured: question g (only negated checks)" in format_table(result)


def test_a_violation_still_blocks_a_set_with_unmeasured_questions():
    judge = {"checks": [{"type": "judge", "criterion": "names the capital"}]}
    result = run([_q("j", judge), _q("1", {"checks": [{"type": "regex", "values": [".*"]}]})])
    assert result["status"] == "blocked"


def test_a_repeated_question_id_grades_each_row_with_its_own_block():
    weak = {"checks": [{"type": "match", "values": ["Paris"]}], "threshold": 0.0}  # accepts everything
    strong = {"checks": [{"type": "exact", "value": "Madrid"}]}
    result = run([_q("a", weak), _q("a", strong)])
    assert result["status"] == "blocked"
    assert {v["attack"] for v in result["violations"]} == {"wrong", "empty", "hedge"}
    assert [r["canary"] for r in result["questions"]] == [True, True]
    assert result["summary"]["questions"] == 2 and result["summary"]["violations"] == 3


def test_the_artifact_names_the_judge_that_graded_the_judge_checks(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [_q("1", _match("Paris"))])
    monkeypatch.setattr("dokimi.eval.judge.Judge", lambda **kw: SimpleNamespace(llm=None))
    out = tmp_path / "judged.json"
    cmd_eval_redteam(_args(tmp_path / "questions.json", out=out, judge=("openai", "judge-x")))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert (saved["judge_model"], saved["judge_provider"]) == ("judge-x", "openai")


def test_the_artifact_has_no_judge_without_the_judge_flag(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [_q("1", _match("Paris"))])
    out = tmp_path / "plain.json"
    cmd_eval_redteam(_args(tmp_path / "questions.json", out=out))
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["judge_model"] is None and saved["judge_provider"] is None


def test_the_judge_ask_sends_the_prompt_to_the_named_model_and_returns_the_reply(monkeypatch):
    from dokimi.eval.judge import judge_ask

    made, sent = {}, {}

    class FakeLLM:
        def invoke(self, prompt, draw=0, cache_if=None):
            sent.update(prompt=prompt, draw=draw)
            return SimpleNamespace(content="YES")

    def fake_judge(**kw):
        made.update(kw)
        return SimpleNamespace(llm=FakeLLM())

    monkeypatch.setattr("dokimi.eval.judge.Judge", fake_judge)
    assert judge_ask("judge-x", "openai")("is it covered?", draw=2) == "YES"
    assert made == {"model": "judge-x", "provider": "openai"} and sent == {"prompt": "is it covered?", "draw": 2}


def _many_violations():
    return run([_q(str(i), {"checks": [{"type": "regex", "values": [".*"]}]}) for i in range(8)])  # 24 violations


def test_the_table_names_dash_o_when_it_cuts_the_violations_and_no_artifact_is_written():
    table = format_table(_many_violations())
    assert "... 4 more violations; rerun with --out FILE to list them all" in table
    assert "in the artifact" not in table


def test_the_table_points_to_the_artifact_when_dash_o_was_given(tmp_path):
    table = format_table(_many_violations(), output=tmp_path / "r.json")
    assert "... 4 more violations in the artifact" in table
    assert "rerun with --out" not in table


def test_the_command_prints_the_cut_violations_with_the_output_path_it_wrote(tmp_path, monkeypatch, capsys):
    _patch_questions(monkeypatch, [_q(str(i), {"checks": [{"type": "regex", "values": [".*"]}]}) for i in range(8)])
    with pytest.raises(SystemExit):
        cmd_eval_redteam(_args(tmp_path / "questions.json", out=tmp_path / "r.json"))
    assert "4 more violations in the artifact" in capsys.readouterr().out


@pytest.mark.parametrize("check", [
    {"type": "match", "value": "Paris", "location": "exact"},
    {"type": "json", "value": "Paris"},
    {"type": "json", "value": 3},
    {"type": "exact", "value": "Paris"},
    {"type": "match", "value": "Paris", "location": "begin"},
    {"type": "choice", "values": ["b", "a"]},
])
def test_the_canary_for_each_literal_check_is_accepted_by_the_grader(check):
    from dokimi.eval.reference_checks import grade

    grading = {"checks": [check]}
    assert grade(grading, "Q?", canary_for(grading)).passed is True


def test_the_canary_is_the_bare_value_for_a_whole_answer_match_and_a_json_string():
    assert canary_for({"checks": [{"type": "match", "value": "Paris", "location": "exact"}]}) == "Paris"
    assert canary_for({"checks": [{"type": "json", "value": "Paris"}]}) == '"Paris"'


def _covered(covered):
    """A judge that finds the point covered (or absent) in both polarities, consistently."""
    from dokimi.eval.checklist import ASK_COVERED

    def ask(prompt, draw=0, cache_if=None):
        return "YES" if (ASK_COVERED in prompt) == covered else "NO"

    return ask


_JUDGE_ONLY = {"checks": [{"type": "judge", "criterion": "names the capital"}]}


def test_a_judge_check_is_scored_when_an_ask_is_given_and_a_judge_that_rejects_every_attack_passes():
    result = run([_q("1", _JUDGE_ONLY)], ask=_covered(False))
    assert result["status"] == "pass" and result["summary"]["unscored"] == 0
    assert all(p["passed"] is False for p in result["questions"][0]["probes"].values())


def test_a_judge_that_accepts_every_answer_blocks_the_run():
    result = run([_q("1", _JUDGE_ONLY)], ask=_covered(True))
    assert result["status"] == "blocked"
    assert {v["attack"] for v in result["violations"]} == {"wrong", "empty", "hedge"}


def test_the_judge_flag_grades_judge_checks_with_the_named_model(tmp_path, monkeypatch):
    made = {}

    def fake_judge(**kw):
        made.update(kw)
        return SimpleNamespace(llm=SimpleNamespace(invoke=lambda prompt, draw=0, cache_if=None:
                                                   SimpleNamespace(content=_covered(True)(prompt))))

    _patch_questions(monkeypatch, [_q("1", _JUDGE_ONLY)])
    monkeypatch.setattr("dokimi.eval.judge.Judge", fake_judge)
    out = tmp_path / "judged.json"
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json", out=out, judge=("openai", "judge-x")))
    assert exc.value.code == 1  # the stub judge accepts every attack
    assert made == {"model": "judge-x", "provider": "openai"}
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["status"] == "blocked" and saved["summary"]["unscored"] == 0


def test_without_the_judge_flag_no_judge_is_built_and_judge_checks_stay_unscored(tmp_path, monkeypatch):
    def refuse(**kw):
        raise AssertionError("a judge was built without --judge")

    _patch_questions(monkeypatch, [_q("1", _JUDGE_ONLY)])
    monkeypatch.setattr("dokimi.eval.judge.Judge", refuse)
    out = tmp_path / "plain.json"
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json", out=out))
    assert exc.value.code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["unscored"] == 3


@pytest.mark.parametrize("grading", [
    {"checks": [{"type": "match", "values": []}]},
    {"checks": [{"type": "match", "values": [""]}]},
    {"checks": []},
    None,
])
def test_a_degenerate_grading_block_is_refused_not_passed(grading):
    with pytest.raises(ValueError, match="cannot be graded"):
        run([_q("1", grading)])


def test_an_empty_question_list_is_inconclusive_not_pass():
    result = run([])
    assert result["status"] == "inconclusive" and result["summary"]["questions"] == 0


def test_command_exits_2_with_the_problem_when_a_grading_block_cannot_be_graded(tmp_path, monkeypatch, capsys):
    _patch_questions(monkeypatch, [_q("7", {"checks": [{"type": "match", "values": []}]})])
    out = tmp_path / "redteam.json"
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json", out=out))
    assert exc.value.code == 2
    assert "7: check 0: match has no values" in capsys.readouterr().err
    assert not out.exists()


def test_command_exits_1_on_an_empty_question_set(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [])
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json"))
    assert exc.value.code == 1


def test_a_blocked_run_still_writes_the_artifact(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [_q("1", {"checks": [{"type": "regex", "values": [".*"]}]})])
    out = tmp_path / "blocked.json"
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json", out=out))
    assert exc.value.code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "blocked"


def test_an_inconclusive_run_still_writes_the_artifact(tmp_path, monkeypatch):
    _patch_questions(monkeypatch, [{"id": "1", "question": "Capital of France?"}])
    out = tmp_path / "inconclusive.json"
    with pytest.raises(SystemExit) as exc:
        cmd_eval_redteam(_args(tmp_path / "questions.json", out=out))
    assert exc.value.code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "inconclusive"


@pytest.mark.parametrize("check", [
    {"type": "exact", "values": ["Paris", "Lyon"]},
    {"type": "match", "values": ["Paris", "Lyon"], "location": "exact"},
    {"type": "json", "values": ["Paris", "Lyon"]},
])
def test_aliases_of_a_whole_answer_check_are_stuffed_bare_and_the_grader_rejects_the_list(check):
    # The list equals none of the values, so a grader that accepts it is wrong; the real one does not.
    assert redteam.attacks({"checks": [check]})["stuffed"] == "Paris Lyon"
    result = run([_q("1", {"checks": [check]})])
    assert result["status"] == "pass" and result["questions"][0]["probes"]["stuffed"]["passed"] is False


@pytest.mark.parametrize("value", ["-.5", "+.5"])
@pytest.mark.parametrize("location", ["end", "begin", "any", "exact"])
def test_a_numeric_target_with_no_digit_before_the_point_gets_a_canary_the_numeric_matcher_reads(value, location):
    # The number regex reads "-.5" as 5 (the sign has no digit after it), so the raw spelling is no canary.
    from dokimi.eval.reference_checks import grade

    grading = {"checks": [{"type": "match", "value": value, "numeric": True, "location": location}]}
    assert grade(grading, "Q?", canary_for(grading)).passed is True
    assert run([_q("1", grading)])["status"] == "pass"


@pytest.mark.parametrize("check", [
    {"type": "exact", "values": ["b", "a"]},  # "b a" is "b" once the article is dropped
    {"type": "fuzzy", "values": ["Paris", "Lyon"]},
])
def test_a_list_the_grader_accepts_as_a_value_is_no_stuffing_attack(check):
    assert redteam.attacks({"checks": [check]})["stuffed"] is None
    result = run([_q("1", {"checks": [check]})])
    assert result["status"] == "pass" and result["violations"] == []


@pytest.mark.parametrize("strip", [True, False])
def test_a_stuffed_list_at_the_end_picks_decoys_that_do_not_complete_a_value(strip):
    # Each decoy alone is rejected, but "Paris QZX-BOGUS-REPLY-4096" ends the answer and is a value.
    check = {"type": "match", "values": ["Paris QZX-BOGUS-REPLY-4096", "Paris"], "strip": strip}
    result = run([_q("1", {"checks": [check]})])
    assert result["status"] == "pass" and result["violations"] == []
    assert result["questions"][0]["probes"]["stuffed"]["passed"] is False




def test_judge_takes_a_provider_model_reference_and_a_bad_one_is_a_usage_error(capsys):
    from dokimi.cli import parse_args

    args = parse_args(["eval", "redteam", "q.json", "--judge", "openai/gpt-x", "--out", "r.json"])
    assert (args.judge, args.out) == (("openai", "gpt-x"), "r.json")
    assert parse_args(["eval", "redteam", "q.json"]).judge is None
    for bad in ("gpt-x", "nosuch/gpt-x", "openai/"):
        with pytest.raises(SystemExit) as exc:
            parse_args(["eval", "redteam", "q.json", "--judge", bad])
        assert exc.value.code == 2, bad
