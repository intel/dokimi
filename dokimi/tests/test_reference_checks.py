# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the reference-check grader (``dokimi.eval.reference_checks``), ``eval reference``
(``dokimi.eval.reference_run``) and native-log import (``task_formats.imports.native_logs``).

Native semantics are the upstream ones the importers cite: Inspect ``match_str`` / ``exact`` /
``choice`` (inspect-ai 0.3.283), OpenAI evals ``Match`` (``startswith``) and ``Includes``,
promptfoo ``contains``/``equals``/``is-json`` (0.125.0). No model is called: the judge is a
fake ``ask`` that answers the checklist's two polarities.
"""

from __future__ import annotations

import json

import pytest

from dokimi.artifacts import save_artifact
from dokimi.eval.checklist import ASK_COVERED
from dokimi.eval.reference_checks import chosen_letters, grade, match_text, parse_json, validate_grading
from dokimi.eval.reference_run import artifact, grading_by_id, score
from dokimi.task_formats.imports.native_logs import read_log


def _ask(covered: bool):
    """A judge that says the point is covered (or absent) in both polarities, consistently."""
    def ask(prompt, draw=0, cache_if=None):
        return "YES" if (ASK_COVERED in prompt) == covered else "NO"
    return ask


def _split(prompt, draw=0, cache_if=None):
    """A judge that says YES to both polarities: covered and absent at once."""
    return "YES"


def _one(check, answer, ask=None):
    return grade({"checks": [check]}, "Q?", answer, ask)


# --- deterministic checks ------------------------------------------------------------------------


@pytest.mark.parametrize("answer,location,expected", [
    ("The answer is Paris.", "end", True),     # trailing punctuation stripped
    ("paris is the answer", "begin", True),    # case folded
    ("I think Paris, yes", "end", False),
    ("I think Paris, yes", "any", True),
    (" Paris ", "exact", True),
])
def test_match_text_follows_inspect_match_str(answer, location, expected):
    assert match_text(answer, "Paris", location=location) is expected


def test_match_numeric_compares_the_number_at_the_location():
    assert match_text("so x = 42.0", "42", location="end", numeric=True) is True
    assert match_text("so x = 43", "42", location="end", numeric=True) is False


def test_openai_match_is_case_sensitive_startswith():
    check = {"type": "match", "values": ["N"], "location": "begin", "ignore_case": False,
             "ignore_punctuation": False, "strip": False}
    assert _one(check, "N, he was not").passed is True
    assert _one(check, "no").passed is False
    assert _one(check, " N").passed is False  # evals does not strip


def test_exact_uses_squad_normalization():
    assert _one({"type": "exact", "values": ["The Eiffel Tower"]}, "eiffel tower!").passed is True
    assert _one({"type": "exact", "values": ["Eiffel Tower"]}, "the Eiffel Tower in Paris").passed is False


def test_fuzzy_is_mutual_containment_after_normalizing():
    assert _one({"type": "fuzzy", "values": ["Leonardo da Vinci"]}, "Da Vinci").passed is True
    assert _one({"type": "fuzzy", "values": ["Leonardo da Vinci"]}, "Michelangelo").passed is False


def test_mode_all_and_negate():
    all_of = {"type": "match", "values": ["red", "blue"], "location": "any", "mode": "all"}
    assert _one(all_of, "red and blue").passed is True
    assert _one(all_of, "red only").passed is False
    assert _one({"type": "match", "values": ["sorry"], "location": "any", "negate": True}, "Sorry!").passed is False


def test_regex_check_and_a_bad_pattern():
    assert _one({"type": "regex", "values": [r"\d{3}-\d{4}"]}, "call 555-1234").passed is True
    with pytest.raises(ValueError, match="does not compile"):
        _one({"type": "regex", "values": ["("]}, "x")


@pytest.mark.parametrize("answer,letters", [
    ("ANSWER: B", {"B"}),
    ("Reasoning...\nANSWER: **c**", {"C"}),
    ("ANSWER: (D)", {"D"}),
    ("ANSWER: A, C", {"A", "C"}),
])
def test_chosen_letters_parse_inspect_answer_lines(answer, letters):
    assert chosen_letters(answer) == frozenset(letters)


def test_choice_needs_exactly_the_target_letters():
    check = {"type": "choice", "values": ["B"]}
    assert _one(check, "ANSWER: B").passed is True
    assert _one(check, "ANSWER: A").passed is False
    no_line = _one(check, "B is right")
    assert no_line.passed is False
    assert no_line.checks[0].detail == "picked nothing"


def test_json_checks_parse_compare_and_contain():
    assert parse_json('```json\n{"a": 1}\n```') == (True, {"a": 1})
    assert _one({"type": "json", "values": [{"a": 1}]}, '{"a": 1}').passed is True
    assert _one({"type": "json", "values": [{"a": 1}]}, '{"a": 2}').passed is False
    assert _one({"type": "json", "values": []}, "not json").passed is False
    assert _one({"type": "json", "values": [], "contains": True}, 'Here: {"ok": true} done').passed is True


# --- judge, unsupported, weights -----------------------------------------------------------------


def test_judge_check_uses_the_checklist_judge_both_polarities():
    check = {"type": "judge", "criterion": "Mentions leprosy"}
    assert _one(check, "Leprosy", _ask(True)).passed is True
    assert _one(check, "Measles", _ask(False)).passed is False
    split = _one(check, "?", _split)
    assert split.score == 0.5
    assert split.passed is False


def test_judge_without_ask_and_unsupported_are_not_counted():
    g = grade({"checks": [{"type": "judge", "reference": "1996"}, {"type": "unsupported", "native": "latency"},
                          {"type": "match", "values": ["1996"], "location": "any"}]}, "Q?", "In 1996.")
    assert [c.passed for c in g.checks] == [None, None, True]
    assert g.score == 1.0
    assert g.as_dict()["aggregate"] == 100.0


def test_weights_and_threshold_set_the_verdict():
    grading = {"threshold": 0.7, "checks": [
        {"type": "match", "values": ["a"], "location": "any", "weight": 3},
        {"type": "match", "values": ["zzz"], "location": "any", "weight": 1}]}
    g = grade(grading, "Q?", "a")
    assert g.score == 0.75
    assert g.passed is True
    assert grade({**grading, "threshold": 0.8}, "Q?", "a").passed is False


def test_missing_answer_is_unscored_not_failed():
    g = grade({"checks": [{"type": "match", "values": ["x"], "location": "any"}]}, "Q?", None)
    assert g.score is None
    assert g.passed is None


def test_validate_grading_names_the_problem():
    assert validate_grading({"checks": [{"type": "match", "values": ["x"]}]}) == []
    assert validate_grading({"checks": []}) == ["grading has no checks"]
    assert validate_grading({"checks": [{"type": "nope"}]}) == ["check 0: unknown type 'nope'"]
    assert validate_grading({"checks": [{"type": "exact"}]}) == ["check 0: exact has no values"]


@pytest.mark.parametrize("block, problem", [
    ({"checks": [{"type": "regex", "values": ["(unclosed"]}]}, "regex '(unclosed' does not compile"),
    ({"checks": [{"type": "match", "values": ["x"], "location": "middle"}]}, "match location must be one of"),
    ({"checks": [{"type": "match", "values": ["x"], "mode": "ALL"}]}, "mode must be 'any' or 'all'"),
    ({"checks": [{"type": "match", "values": ["x"], "weight": "high"}]}, "weight is not a number"),
    ({"checks": [{"type": "judge"}]}, "a judge check needs a criterion or a reference"),
    ({"checks": [{"type": "exact", "values": ["4"]}], "threshold": "most"}, "threshold is not a number"),
])
def test_validate_grading_catches_what_grading_would_raise_on(block, problem):
    # Each of these used to pass validation and then abort `eval reference` mid-run.
    found = validate_grading(block)
    assert len(found) == 1 and problem in found[0]


# --- eval reference ------------------------------------------------------------------------------

_QUESTIONS = [
    {"id": "1", "question": "Capital of France?", "grading": {"checks": [
        {"type": "match", "values": ["Paris"], "location": "any"}]}},
    {"id": "2", "question": "2+2?", "grading": {"checks": [{"type": "exact", "values": ["4"]}]}},
    {"id": "3", "question": "No grading block"},
]


def test_score_binary_answers_gives_eval_v1(tmp_path):
    data = {"answers": [
        {"question_id": "1", "question_text": "Capital of France?",
         "with_docs": {"answer": "Paris."}, "without_docs": {"answer": "Lyon"}},
        {"question_id": "2", "question_text": "2+2?", "with_docs": None, "without_docs": {"answer": "4"}},
        {"question_id": "3", "question_text": "No grading block", "without_docs": {"answer": "x"}},
    ]}
    result = score(data, _QUESTIONS)
    assert result.missing == ["3"]
    assert result.tally["without_docs"].as_dict() == {"n": 2, "unscored": 0, "pass_rate": 0.5, "mean_score": 0.5}
    kind, payload = artifact(result, data, judge_model=None, judge_provider=None, questions_path="q.json")
    assert kind == "eval"
    first = payload["evaluations"][0]
    assert (first["with_docs"]["passed"], first["without_docs"]["passed"], first["delta"]) == (True, False, 100.0)
    assert payload["run_metadata"]["ungraded_questions"] == 1
    written = save_artifact(kind, payload, tmp_path / "eval.json")  # validates eval.v1
    assert written["evaluations"][1]["without_docs"]["scorer"] == "reference"


def test_score_arms_run_gives_arms_artifact(tmp_path):
    data = {"baseline_arm": "baseline", "arms": ["baseline", "skill"], "answers": [
        {"question_id": "1", "question_text": "Capital of France?",
         "arms": {"baseline": {"answer": "Lyon"}, "skill": {"answer": "It is Paris"}}}]}
    result = score(data, _QUESTIONS)
    kind, payload = artifact(result, data, judge_model="m", judge_provider="p", questions_path="q.json")
    assert kind == "arms"
    scores = payload["evaluations"][0]["scores"]
    assert (scores["baseline"]["passed"], scores["skill"]["passed"]) == (False, True)
    save_artifact(kind, payload, tmp_path / "arms.json")  # validates arms.v1


def test_no_retrieval_placeholder_is_unscored_not_graded():
    polite = [{"id": "1", "question": "q", "grading": {"checks": [
        {"type": "match", "values": ["sorry"], "location": "any", "negate": True}]}}]
    data = {"answers": [{"question_id": "1", "question_text": "q",
                         "with_docs": {"answer": "[FALLBACK: No relevant documentation found]",
                                       "doc_source": "fallback_none"},
                         "without_docs": {"answer": "Sorry, no idea"}}]}
    result = score(data, polite)
    assert result.tally["with_docs"].as_dict()["unscored"] == 1  # the negated check would have passed it
    assert result.evaluations[0]["delta"] is None


def test_grading_by_id_refuses_an_ungradable_block():
    with pytest.raises(ValueError, match="2: check 0: unknown type"):
        grading_by_id([{"id": "2", "question": "q", "grading": {"checks": [{"type": "bogus"}]}}])


# --- native logs ---------------------------------------------------------------------------------


def test_read_inspect_json_log(tmp_path):
    log = {"eval": {"model": "bedrock/haiku"}, "samples": [
        {"id": 1, "input": "Q1", "output": {"completion": "ANSWER: A"}, "scores": {"choice": {"value": "C"}}},
        {"id": 2, "input": [{"role": "user", "content": "Q2"}], "output": {"completion": "meh"},
         "scores": {"model_graded_qa": {"value": "P"}}},
    ]}
    path = tmp_path / "log.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    run = read_log(path)
    assert run.tool == "inspect"
    assert [a["question_id"] for a in run.answers] == ["1", "2"]
    assert run.answers[1]["question_text"] == "Q2"
    blocks = [e["without_docs"] for e in run.evaluations]
    assert [(b["aggregate"], b["passed"]) for b in blocks] == [(100.0, True), (50.0, False)]
    save_artifact("answers", run.answers_artifact(), tmp_path / "a.json")
    save_artifact("eval", run.eval_artifact(), tmp_path / "e.json")


def test_errored_inspect_sample_stays_unscored_in_eval_reference(tmp_path):
    log = {"eval": {"model": "m"}, "samples": [
        {"id": "q1", "input": "Q1", "output": {"completion": "Paris"}, "scores": {"match": {"value": "C"}}},
        {"id": "q2", "input": "Q2", "output": {"completion": ""}, "scores": {},
         "error": {"message": "RuntimeError('throttled')", "traceback": "..."}},
    ]}
    path = tmp_path / "log.json"
    path.write_text(json.dumps(log), encoding="utf-8")
    run = read_log(path)
    save_artifact("answers", run.answers_artifact(), tmp_path / "a.json")
    checks = {"checks": [{"type": "match", "values": ["paris"], "location": "any"}]}
    questions = [{"id": q, "question": q, "grading": checks} for q in ("q1", "q2")]
    tally = score(run.answers_artifact(), questions).tally["without_docs"].as_dict()
    assert (tally["n"], tally["unscored"], tally["pass_rate"]) == (1, 1, 1.0)


def test_read_promptfoo_output_keys_by_test_and_prompt(tmp_path):
    rows = [{"testIdx": t, "promptIdx": p, "success": t == 0, "score": 1 if t == 0 else 0,
             "response": {"output": f"out{t}{p}"}, "prompt": {"raw": f"prompt{p}"}, "provider": {"id": "x:y"}}
            for t in (0, 1) for p in (0, 1)]
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"results": {"results": rows}}), encoding="utf-8")
    run = read_log(path)
    assert [a["question_id"] for a in run.answers] == ["0.p0", "0.p1", "1.p0", "1.p1"]
    assert [e["without_docs"]["passed"] for e in run.evaluations] == [True, True, False, False]


def test_read_promptfoo_refuses_several_providers(tmp_path):
    rows = [{"testIdx": 0, "provider": {"id": a}} for a in ("a", "b")]
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"results": {"results": rows}}), encoding="utf-8")
    with pytest.raises(ValueError, match="2 providers"):
        read_log(path)
