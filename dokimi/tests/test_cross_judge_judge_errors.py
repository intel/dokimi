# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A re-scoring judge that fails says so, and one that scored nothing fails the run.

``score_measure`` keeps going past a failed judge call (the answer drops out of the
pairing), but it used to drop the error with it: a judge with no key, an unknown
model or a dead endpoint gave ``NOT MEASURED``, exit 0, and no reason anywhere.
"""

import json
import logging

import pytest

from dokimi.cli import build_parser
from dokimi.eval import cross_judge as cj
from dokimi.eval import judge as judge_mod

KEY_ERROR = "litellm.AuthenticationError: Missing Anthropic API Key - set ANTHROPIC_API_KEY"


def _records(n=3):
    return [{"question_id": f"Q{i}", "question_text": "How do I use X?", "arms": {
        "baseline": {"answer": f"b{i}"}, "skill": {"answer": f"s{i}"}}} for i in range(n)]


def _scorer(failing):
    """Scores 50, except an answer in *failing*, which raises naming that answer."""
    def score(library, question, answer, ground_truth, context):
        if answer in failing:
            raise RuntimeError(f"judge down on {answer}")
        return 50.0
    return score


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.name == cj.__name__]


def test_score_measure_counts_the_unscored_answers_and_keeps_the_first_error(caplog):
    with caplog.at_level(logging.WARNING, logger="dokimi.eval.cross_judge"):
        m = cj.score_measure(_records(), _scorer({"s1", "b2"}), name="p/m", library="L", concurrency=4)

    assert (m.asked, m.unscored) == (6, 2)
    # The first failure in answer order, whichever thread finished first.
    assert m.first_error == "RuntimeError: judge down on s1"
    assert _warnings(caplog) == [
        "p/m (aggregate): 2 of 6 answers unscored, first error: RuntimeError: judge down on s1"]


def test_score_measure_with_every_answer_scored_warns_nothing(caplog):
    with caplog.at_level(logging.WARNING, logger="dokimi.eval.cross_judge"):
        m = cj.score_measure(_records(), _scorer(set()), name="p/m", library="L")

    assert (m.asked, m.unscored, m.first_error) == (6, 0, None)
    assert _warnings(caplog) == []


def test_the_reason_is_the_first_line_of_the_message_without_a_repeated_type():
    multiline = RuntimeError("Connection error.\nTraceback (most recent call last): ...")
    m = cj.score_measure(_records(1), lambda *a: (_ for _ in ()).throw(multiline), name="p/m", library="L")
    assert m.first_error == "RuntimeError: Connection error."

    m = cj.score_measure(_records(1), lambda *a: (_ for _ in ()).throw(RuntimeError("\n  Connection error.\n")),
                         name="p/m", library="L")
    assert m.first_error == "RuntimeError: Connection error."  # the first line with text
    m = cj.score_measure(_records(1), lambda *a: (_ for _ in ()).throw(RuntimeError()), name="p/m", library="L")
    assert m.first_error == "RuntimeError"  # no message: the type alone

    m = cj.score_measure(_records(1), lambda *a: (_ for _ in ()).throw(RuntimeError(KEY_ERROR)),
                         name="p/m", library="L")
    assert m.first_error == "RuntimeError: " + KEY_ERROR  # the type is not in the message

    class AuthenticationError(Exception):
        pass

    m = cj.score_measure(_records(1), lambda *a: (_ for _ in ()).throw(AuthenticationError(KEY_ERROR)),
                         name="p/m", library="L")
    assert m.first_error == KEY_ERROR  # litellm already names its type


def test_a_measure_read_from_a_file_asked_no_judge():
    m = cj.coverage_measure({"questions": [{"question_id": "Q1", "arms": {"skill": {"coverage": None}}}]}, "c")
    assert (m.asked, m.unscored, m.first_error) == (0, 0, None)


def _cross_judge(tmp_path, monkeypatch, failing, *extra, data=None):
    """``eval judge agreement --judges anthropic/m`` with a judge that raises on *failing* answers.

    Returns ``(exit code, stderr, the written JSON report)``; the code is 0 when it did not exit.
    """
    class FailingJudge:
        def __init__(self, **_):
            pass

        def score_answer(self, *, answer, **_):
            if failing is all or answer in failing:
                raise RuntimeError(KEY_ERROR)
            return {"aggregate": 50.0}

    monkeypatch.setattr(judge_mod, "Judge", FailingJudge)
    answers = tmp_path / "run.json"
    answers.write_text(json.dumps(data or {"library_name": "L", "answers": _records()}), encoding="utf-8")
    out = tmp_path / "cj.json"
    args = build_parser().parse_args(["eval", "judge", "agreement", "--answers", str(answers), "--judges", "anthropic/m",
                                      "--output", str(out), *extra])
    code = 0
    try:
        args.func(args)
    except SystemExit as exc:
        code = exc.code
    return code, json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.parametrize("strict", [[], ["--strict"]])  # a failed run, not --strict's NOT MEASURED exit 1
def test_a_judge_that_scored_nothing_exits_2_after_writing_the_report(tmp_path, monkeypatch, capsys, strict):
    code, report = _cross_judge(tmp_path, monkeypatch, all, "--no-stored", *strict)
    err = capsys.readouterr().err

    assert code == 2
    assert f"Error: anthropic/m (aggregate) scored none of its 6 answers, first error: RuntimeError: {KEY_ERROR}\n" \
        in err
    assert report["summary"]["arms"]["skill"]["label"] == "NOT MEASURED"
    assert report["unscored"] == [{"measure": "anthropic/m", "kind": "aggregate", "asked": 6, "unscored": 6,
                                   "first_error": f"RuntimeError: {KEY_ERROR}"}]
    assert (tmp_path / "cj.md").read_text(encoding="utf-8").endswith(
        f"`anthropic/m` (aggregate): 6 of 6 answers unscored, first error: RuntimeError: {KEY_ERROR}\n")


@pytest.mark.parametrize("failing, compared", [
    ({"s0"}, 2),
    ({"b0", "s0", "b1", "s1", "b2"}, 0),  # all but one: the edge of "scored none"
])
def test_a_judge_that_scored_some_answers_warns_and_keeps_exit_0(tmp_path, monkeypatch, caplog, capsys,
                                                                 failing, compared):
    with caplog.at_level(logging.WARNING, logger="dokimi.eval.cross_judge"):
        code, report = _cross_judge(tmp_path, monkeypatch, failing, "--no-stored")
    note = f"(aggregate): {len(failing)} of 6 answers unscored, first error: RuntimeError: {KEY_ERROR}"

    assert code == 0
    assert "Error:" not in capsys.readouterr().err
    assert f"anthropic/m {note}" in caplog.text
    assert report["unscored"][0]["unscored"] == len(failing)
    assert report["summary"]["arms"]["skill"]["rows"][0]["n"] == compared
    assert (tmp_path / "cj.md").read_text(encoding="utf-8").endswith(f"`anthropic/m` {note}\n")


def test_a_scored_nothing_judge_fails_the_run_even_when_the_stored_measure_carries_the_label(
        tmp_path, monkeypatch, capsys):
    data = {"library_name": "L", "answers": _records(8), "evaluations": [
        {"question_id": f"Q{i}", "scores": {"baseline": {"aggregate": 50}, "skill": {"aggregate": 90}}}
        for i in range(8)]}
    code, report = _cross_judge(tmp_path, monkeypatch, all, "--strict", data=data)

    assert report["summary"]["arms"]["skill"]["label"] == "CONSISTENT + (one judge)"  # --strict alone passes
    assert code == 2
    assert "Error: anthropic/m (aggregate) scored none of its 16 answers" in capsys.readouterr().err


def test_a_clean_run_has_no_unscored_section(tmp_path, monkeypatch):
    code, report = _cross_judge(tmp_path, monkeypatch, set(), "--no-stored")
    assert code == 0
    assert report["unscored"] == []
    assert "unscored" not in (tmp_path / "cj.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("both", [False, True])
def test_every_measure_of_a_dead_judge_is_named(tmp_path, monkeypatch, capsys, both):
    questions = tmp_path / "q.json"
    topics = ["X is configured with a single call before the first use"]
    questions.write_text(json.dumps([{"id": f"Q{i}", "question": "How do I use X?", "expected_topics": topics}
                                     for i in range(3)]), encoding="utf-8")
    extra = ["--no-stored", "--grounded", "--questions", str(questions)] + (["--both"] if both else [])
    code, _ = _cross_judge(tmp_path, monkeypatch, all, *extra)
    err = capsys.readouterr().err

    assert code == 2
    assert ("anthropic/m (aggregate) scored none" in err) is both
    assert "anthropic/m (grounded) scored none" in err
