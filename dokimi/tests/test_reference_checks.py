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
import time

import pytest

from dokimi.artifacts import save_artifact
from dokimi.eval.checklist import ASK_COVERED
from dokimi.eval.reference_checks import (
    REGEX_TIMEOUT, chosen_letters, grade, match_text, parse_json, validate_grading,
)
from dokimi.eval.reference_run import ConditionTally, artifact, grading_by_id, score
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


@pytest.mark.parametrize("negate", [False, True])
def test_a_regex_that_backtracks_past_the_timeout_is_unscored(negate):
    # (a|aa)+$ on a run of a's that ends in ! backtracks for minutes under re.
    started = time.monotonic()
    g = _one({"type": "regex", "values": ["(a|aa)+$"], "negate": negate}, "a" * 40 + "!")
    assert time.monotonic() - started < 5 * REGEX_TIMEOUT
    assert g.checks[0].as_dict() == {"type": "regex", "passed": None, "score": None,
                                     "detail": "not scored: regex ran past 1s"}
    assert g.score is None and g.passed is None


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


def test_scored_grade_with_an_unjudged_check_is_counted_in_the_summary():
    # With no judge a match still scores the record, so it is counted; the summary says how many
    # counted records had a judged check left without a verdict, so a pass is not read as judged.
    tally = ConditionTally()
    tally.add(grade({"checks": [{"type": "match", "values": ["1996"], "location": "any"},
                                {"type": "judge", "reference": "1996"}]}, "Q?", "In 1996."))
    assert tally.as_dict() == {"n": 1, "unscored": 0, "errors": 0, "judge_unscored": 1, "check_unscored": 0,
                               "pass_rate": 1.0, "mean_score": 1.0}


def test_a_timed_out_check_next_to_a_pass_is_counted_in_the_summary():
    # The pass is decided on the match alone, so the summary must say the regex had no result.
    tally = ConditionTally()
    tally.add(grade({"checks": [{"type": "regex", "values": ["(a|aa)+$"]},
                                {"type": "match", "values": ["!"], "location": "any"}]}, "Q?", "a" * 40 + "!"))
    assert tally.as_dict() == {"n": 1, "unscored": 0, "errors": 0, "judge_unscored": 0, "check_unscored": 1,
                               "pass_rate": 1.0, "mean_score": 1.0}


def test_a_space_is_a_real_pattern_and_an_unstripped_match_value():
    # Not blank: " " is found only in answers that contain a space, unlike "" or a stripped " ".
    assert validate_grading({"checks": [{"type": "regex", "values": [" "]}]}) == []
    assert validate_grading({"checks": [{"type": "match", "values": [" "], "strip": False}]}) == []
    assert _one({"type": "regex", "values": [" "]}, "nogap").passed is False


def test_a_settled_any_mode_regex_does_not_wait_for_a_later_pattern():
    # The first pattern decides the check, so the timing-out second one is never run.
    started = time.monotonic()
    g = _one({"type": "regex", "values": ["!", "(a|aa)+$"]}, "a" * 40 + "!")
    assert (g.checks[0].passed, g.checks[0].score) == (True, 1.0)
    assert time.monotonic() - started < REGEX_TIMEOUT


def test_an_all_mode_regex_is_not_settled_by_its_first_match():
    # "a" matches, so only the second pattern, which does not, can decide the check.
    assert _one({"type": "regex", "values": ["a", "zzz"], "mode": "all"}, "a").passed is False


def test_the_regex_limit_is_shared_by_every_pattern_of_one_check():
    started = time.monotonic()
    g = _one({"type": "regex", "values": ["(a|aa)+$", "(a|aa)+$"]}, "a" * 40 + "!")
    assert g.checks[0].passed is None
    assert time.monotonic() - started < 1.5 * REGEX_TIMEOUT


def test_a_question_with_one_failing_check_fails_without_a_threshold():
    two = {"checks": [{"type": "match", "values": ["a"], "location": "any"},
                      {"type": "match", "values": ["zzz"], "location": "any"}]}
    assert (grade(two, "Q?", "a").score, grade(two, "Q?", "a").passed) == (0.5, False)


def test_a_zero_weight_check_neither_counts_nor_fails_the_question():
    grading = {"checks": [{"type": "match", "values": ["a"], "location": "any"},
                          {"type": "match", "values": ["zzz"], "location": "any", "weight": 0}]}
    g = grade(grading, "Q?", "a")
    assert (g.score, g.passed, len(g.counted)) == (1.0, True, 1)


def test_weights_and_threshold_set_the_verdict():
    grading = {"threshold": 0.7, "checks": [
        {"type": "match", "values": ["a"], "location": "any", "weight": 3},
        {"type": "match", "values": ["zzz"], "location": "any", "weight": 1}]}
    g = grade(grading, "Q?", "a")
    assert g.score == 0.75
    assert g.passed is True
    assert grade({**grading, "threshold": 0.8}, "Q?", "a").passed is False


def test_grader_edges_that_a_mutation_run_left_unpinned():
    half = {"threshold": 0.5, "checks": [{"type": "match", "values": ["a"], "location": "any"},
                                         {"type": "match", "values": ["zzz"], "location": "any"}]}
    assert grade(half, "Q?", "a").passed is True  # reaching the threshold exactly passes
    sorry = {"type": "judge", "criterion": "Apologizes", "negate": True}
    assert (_one(sorry, "Sorry", _ask(True)).passed, _one(sorry, "Fine", _ask(False)).passed) == (False, True)
    assert _one({"type": "regex", "values": ["paris"], "ignore_case": True}, "PARIS").passed is True
    assert _one({"type": "regex", "values": ["paris"]}, "PARIS").passed is False
    assert _one({"type": "choice", "values": ["A", "C"]}, "ANSWER: A").passed is False
    assert _one({"type": "choice", "values": ["A"]}, "ANSWER: A, C").passed is False
    assert _one({"type": "fuzzy", "values": ["Da Vinci"]}, "Leonardo da Vinci").passed is True
    assert match_text("42 then 7", "42", location="begin", numeric=True) is True
    assert match_text("42 then 7", "42", location="end", numeric=True) is False


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
    ({"checks": [{"type": "match", "values": ["x"], "weight": None}]}, "weight is not a number"),
    ({"checks": [{"type": "match", "values": ["x"], "weight": float("nan")}]}, "weight is not a number"),
    ({"checks": [{"type": "match", "values": ["x"], "weight": float("inf")}]}, "weight is not a number"),
    ({"checks": [{"type": "exact", "values": ["4"]}], "threshold": float("nan")}, "threshold is not a number"),
    ({"checks": [{"type": "exact", "values": ["4"]}], "threshold": float("inf")}, "threshold is not a number"),
    ({"checks": [{"type": ["match"], "values": ["x"]}]}, "unknown type"),
    ({"checks": [{"type": "match", "values": [""]}]}, "match has a blank value"),
    ({"checks": [{"type": "match", "values": ["x", "  "]}]}, "match has a blank value"),
    ({"checks": [{"type": "match", "values": [" "]}]}, "match has a blank value"),
    ({"checks": [{"type": "match", "values": ["..."]}]}, "match has a blank value"),
    ({"checks": [{"type": "match", "values": ["x", "!!"], "strip": False}]}, "match has a blank value"),
    ({"checks": [{"type": "regex", "values": [""]}]}, "regex has a blank value"),
    ({"checks": [{"type": "match", "values": ["x"], "weight": -1}]}, "weight must not be negative"),
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
    assert result.tally["without_docs"].as_dict() == {"n": 2, "unscored": 0, "errors": 0,
                                                      "judge_unscored": 0, "check_unscored": 0,
                                                      "pass_rate": 0.5, "mean_score": 0.5}
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


@pytest.mark.parametrize("value, location", [("...", "end"), ("!!", "any")])
def test_a_punctuation_only_match_value_is_refused_not_passed(value, location):
    # Under the default ignore_punctuation the value is "", which every answer contains.
    check = {"type": "match", "values": [value], "location": location}
    with pytest.raises(ValueError, match="1: check 0: match has a blank value"):
        grading_by_id([{"id": "1", "question": "q", "grading": {"checks": [check]}}])
    kept = {"checks": [{**check, "ignore_punctuation": False}]}
    assert validate_grading(kept) == []
    assert grade(kept, "Q?", "I refuse to answer").passed is False


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


def test_errored_inspect_sample_is_an_error_not_a_score_in_eval_reference(tmp_path):
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
    # The errored sample is counted as an error, apart from the unscored ones, and not in n or the rate.
    assert (tally["n"], tally["unscored"], tally["errors"], tally["pass_rate"]) == (1, 0, 1, 1.0)


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


@pytest.mark.parametrize("reply", ["", "   \n\t", None])
def test_an_empty_reply_is_an_error_not_a_score_in_eval_reference(reply):
    """An empty reply is no answer: never graded (a negated check would pass "" as a 1.0)."""
    negated = {"checks": [{"type": "match", "values": ["i don't know"], "location": "any", "negate": True}]}
    plain = {"checks": [{"type": "match", "values": ["paris"], "location": "any"}]}
    for grading in (negated, plain):
        data = {"baseline_arm": "baseline", "arms": ["baseline", "skill"], "answers": [
            {"question_id": "q1", "question_text": "Capital?",
             "arms": {"baseline": {"answer": "Paris"}, "skill": {"answer": reply}}}]}
        result = score(data, [{"id": "q1", "question": "Capital?", "grading": grading}])
        tally = result.tally["skill"].as_dict()
        assert (tally["n"], tally["errors"], tally["pass_rate"], tally["mean_score"]) == (0, 1, None, None)


# --- a judge that fails makes the answer an error, not a pass on the other checks -----------------

def _judge_raises(prompt, draw=0, cache_if=None):
    raise TimeoutError()


def _judge_unparsed(prompt, draw=0, cache_if=None):
    return "I cannot tell"


_MIXED = {"checks": [{"type": "match", "values": ["paris"], "location": "any"},
                     {"type": "judge", "criterion": "names the capital"}], "threshold": 0.5}


@pytest.mark.parametrize("ask,error", [
    (_judge_raises, "judge: TimeoutError"),
    (_judge_unparsed, "judge: no YES or NO in the reply"),
])
def test_a_judge_failure_is_an_error_not_a_pass_decided_by_the_other_checks(ask, error):
    g = grade(_MIXED, "Q?", "Paris", ask)
    assert (g.score, g.passed, g.error) == (None, None, error)
    assert g.as_dict()["error"] == error
    tally = ConditionTally()
    tally.add(g)
    assert (tally.n, tally.errors, tally.unscored) == (0, 1, 0)


def test_judge_checks_after_a_failed_judge_check_make_no_paid_calls():
    """The grade is an error once a judge call fails, so the judge checks after it are not asked."""
    four = {"checks": [{"type": "judge", "criterion": f"names point {i}"} for i in range(4)]}
    counted = {"one": [], "four": []}

    def counting(key):
        def ask(prompt, draw=0, cache_if=None):
            counted[key].append(prompt)
            raise TimeoutError()
        return ask

    grade({"checks": four["checks"][:1]}, "Q?", "Paris", counting("one"))
    g = grade(four, "Q?", "Paris", counting("four"))
    assert len(counted["four"]) == len(counted["one"]) > 0
    assert g.error == "judge: TimeoutError"
    assert [c.error for c in g.checks[1:]] == [None, None, None]
    assert [c.detail for c in g.checks[1:]] == ["not judged: an earlier judge check failed"] * 3


def test_eval_reference_counts_a_judge_failure_as_an_error_for_every_arm():
    data = {"baseline_arm": "baseline", "arms": ["baseline", "skill"], "answers": [
        {"question_id": "q1", "question_text": "Capital?",
         "arms": {"baseline": {"answer": "Paris"}, "skill": {"answer": "Paris"}}}]}
    result = score(data, [{"id": "q1", "question": "Capital?", "grading": _MIXED}], ask=_judge_raises)
    for arm in ("baseline", "skill"):
        tally = result.tally[arm].as_dict()
        assert (tally["n"], tally["errors"], tally["pass_rate"]) == (0, 1, None)
        cell = result.evaluations[0]["scores"][arm]
        assert (cell["passed"], cell["aggregate"], cell["error"]) == (None, None, "judge: TimeoutError")


def test_the_failed_judge_check_names_its_error_in_the_cell_and_the_graded_check_has_none():
    data = {"baseline_arm": "baseline", "arms": ["baseline"], "answers": [
        {"question_id": "q1", "question_text": "Capital?", "arms": {"baseline": {"answer": "Paris"}}}]}
    result = score(data, [{"id": "q1", "question": "Capital?", "grading": _MIXED}], ask=_judge_raises)
    match, judge = result.evaluations[0]["scores"]["baseline"]["checks"]
    assert (judge["type"], judge["error"]) == ("judge", "judge: TimeoutError")
    assert match == {"type": "match", "passed": True, "score": 1.0}


def test_a_reference_cell_carries_its_answer_error_as_an_arm_cell_does(tmp_path):
    data = {"baseline_arm": "baseline", "arms": ["baseline", "skill"], "answers": [
        {"question_id": "q1", "question_text": "Capital?",
         "arms": {"baseline": {"answer": "Paris"},
                  "skill": {"answer": None, "error": "generation_failed: HTTP 500"}}}]}
    grading = {"checks": [{"type": "match", "values": ["paris"], "location": "any"}]}
    result = score(data, [{"id": "q1", "question": "Capital?", "grading": grading}])
    kind, payload = artifact(result, data, judge_model=None, judge_provider=None, questions_path="q.json")
    scores = payload["evaluations"][0]["scores"]
    assert scores["skill"]["error"] == "generation_failed: HTTP 500"
    assert "error" not in scores["baseline"]
    save_artifact(kind, payload, tmp_path / "arms.json")  # validates arms.v1 with the error on the cell
