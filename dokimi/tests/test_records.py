# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""eval/records.py, the one reader of answers, eval and arms records.

Every fixture here is a schema-checked artifact (``artifact_fixture``) in the
shape its writer writes: the answerer's ``question_text`` and doc ``snippet``,
the judge's ``{aggregate, <dimensions>}`` blocks, the arm runner's ``arms`` and
``scores``.
"""

import json

import pytest

from dokimi.eval import records
from dokimi.eval.records import BASELINE, CONTEXT, Record, RecordError
from tests.artifact_fixtures import artifact_fixture


def _doc(snippet, relevance=0.8):
    return {"source": "context7", "snippet": snippet, "relevance_score": relevance}


def _answers(*items):
    return artifact_fixture("answers", {"model": "m", "provider": "p", "answers": list(items)})


def _answer(qid, context="use parallel_for", baseline="a guess", *, docs=(), doc_source="context7", **extra):
    with_docs = None if context is None else {
        "answer": context, "retrieved_docs": list(docs), "doc_source": doc_source, "model": "m"}
    return {"question_id": qid, "question_text": f"Question {qid}?", "library_name": "lib",
            "with_docs": with_docs, "without_docs": None if baseline is None else {"answer": baseline},
            **extra}


def _evals(*items):
    return artifact_fixture("eval", {"judge_model": "j", "judge_provider": "p", "evaluations": list(items)})


def _eval(qid, w, wo, **extra):
    block = lambda agg: None if agg is None else {"aggregate": agg, "correctness": agg}  # noqa: E731
    delta = w - wo if w is not None and wo is not None else None
    return {"question_id": qid, "question_text": f"Question {qid}?", "with_docs": block(w),
            "without_docs": block(wo), "delta": delta, **extra}


def _arms(answers, evaluations=None):
    payload = {"library_name": "lib", "model": "m", "provider": "p", "arms": ["baseline", "skill:x"],
               "baseline_arm": "baseline", "total_questions": len(answers), "answers": answers}
    if evaluations is not None:
        payload.update(evaluations=evaluations, judge_model="j", judge_provider="p")
    return artifact_fixture("arms", payload)


# ── binary ──────────────────────────────────────────────────────────────


def test_binary_answers_record_exposes_text_answers_and_context():
    run = records.read(_answers(_answer(
        "q1", docs=[_doc("parallel_for splits a range", 0.7), _doc("")],
        ground_truth_chunk="parallel_for(range, body)")))
    assert (run.shape, run.baseline, run.treatments, run.meta["model"]) == ("binary", BASELINE, (CONTEXT,), "m")
    r = run.records[0]
    assert (r.question_text, r.ground_truth) == ("Question q1?", "parallel_for(range, body)")
    ctx = r.answer(CONTEXT)
    assert ctx.text == "use parallel_for"
    # The answerer writes `snippet`; an empty one keeps its place, as the judge numbers docs.
    assert ctx.context == ["parallel_for splits a range", ""]
    assert ctx.docs[0].relevance == 0.7
    assert r.answer(BASELINE).text == "a guess"
    assert r.scores == {} and r.conditions == (CONTEXT, BASELINE)


def test_binary_eval_record_keeps_values_as_written():
    first, second = records.read(_evals(_eval("q1", 80, 60.5), _eval("q2", None, None, error="boom"))).records
    # An int aggregate stays an int: reports print `80`, not `80.0`.
    assert first.score(CONTEXT) == 80 and isinstance(first.score(CONTEXT), int)
    assert first.score(BASELINE) == 60.5 and first.delta == 19.5
    assert first.scores[CONTEXT].raw["correctness"] == 80
    # A null payload is listed but has no score.
    assert second.score(CONTEXT) is None and second.nulls == (CONTEXT, BASELINE)
    assert second.error == "boom" and second.delta is None


def test_a_non_numeric_aggregate_is_unscored_not_an_error():
    data = _evals({"question_id": "q1", "with_docs": {"aggregate": "80"}, "without_docs": {"aggregate": True}})
    r = records.read(data).records[0]
    assert (r.score(CONTEXT), r.score(BASELINE)) == (None, None)


def test_the_panels_question_key_is_read_as_question_text():
    """The panel's eval output writes `question`; it is the same field."""
    r = records.record({"question_id": "q1", "question": "Why?", "delta": None,
                        "with_docs": {"aggregate": 70.0, "agreement_score": 0.9}, "without_docs": None})
    assert r.question_text == "Why?"
    assert r.score(CONTEXT) == 70.0 and r.scores[CONTEXT].agreement == 0.9


def test_retrieval_hit_is_known_only_where_retrieval_was_recorded():
    hit, miss, no_ctx = records.read(_answers(
        _answer("q1", docs=[_doc("x")]),
        _answer("q2", "[FALLBACK: No relevant documentation found]", doc_source="fallback_none"),
        _answer("q3", context=None))).records
    assert (hit.retrieval_hit, miss.retrieval_hit, no_ctx.retrieval_hit) == (True, False, None)
    judged, stamped = records.read(_evals(_eval("q4", 80, 60), _eval("q5", 80, 60, retrieval_hit=False))).records
    # An eval record judged before the judge stamped retrieval_hit says nothing.
    assert (judged.retrieval_hit, stamped.retrieval_hit) == (None, False)


def test_question_ids_stay_as_written_and_join_as_strings():
    zero, seven = records.read(_evals(_eval(0, 80, 60), _eval(7, 70, 60))).records
    assert (zero.question_id, seven.question_id) == (0, 7)  # 0 is an id; ints stay ints
    assert (zero.key, seven.key) == ("0", "7")
    assert records.records([zero])[0] is zero and isinstance(zero, Record)


def test_a_binary_join_prefers_stamped_hits_falls_back_for_metadata_and_keeps_eval_only_questions():
    answers = _answers(
        _answer("q1", docs=[_doc("ctx")], persona="dev", ground_truth_chunk="gt-a"),
        {**_answer("q2", docs=[_doc("ctx")]), "question_text": ""},
    )["answers"]
    evals = _evals(
        _eval("q1", 80, 60, retrieval_hit=False, diagnosis={"kind": "eval"}, category="c-eval"),
        {**_eval("q2", 70, 70), "question_text": "Only the eval has it?", "difficulty": "hard",
         "error": "judge flaked"},
        _eval("q3", 50, 40),
    )["evaluations"]
    q1, q2, q3 = records.join(answers, evals, source="run")
    # (a) the evaluation's stamped retrieval_hit wins over the hit the answer's docs imply
    assert q1.retrieval_hit is False
    assert q1.persona == "dev" and q1.ground_truth == "gt-a" and q1.category == "c-eval"
    assert q1.diagnosis == {"kind": "eval"}
    # answers stay with the answers record, scores come from the evaluation
    assert q1.answer(CONTEXT).text == "use parallel_for"
    assert (q1.score(CONTEXT), q1.score(BASELINE), q1.delta) == (80, 60, 20)
    assert q1.conditions == (CONTEXT, BASELINE)
    assert q1.raw["library_name"] == "lib" and q1.raw["delta"] == 20
    # (b) metadata the answers record lacks falls back to the evaluation
    assert (q2.question_text, q2.difficulty, q2.error) == ("Only the eval has it?", "hard", "judge flaked")
    assert q2.retrieval_hit is True  # nothing stamped: implied by the answer's docs
    # (c) a question only the evaluations have is kept, after the answered ones
    assert q3.key == "q3" and q3.answers == {} and q3.score(CONTEXT) == 50


def test_a_join_refuses_a_duplicate_evaluation():
    with pytest.raises(RecordError, match="duplicate question_id 'q1' in evaluations"):
        records.join([], [_eval("q1", 1, 1), _eval("q1", 2, 2)], source="run")


# ── arms ────────────────────────────────────────────────────────────────


def _arms_run():
    return _arms(
        [{"question_id": "q1", "question_text": "Q1?", "expected_skill": "x", "arms": {
            "baseline": {"answer": "b1", "context_chunks": []},
            "skill:x": {"answer": "s1", "context_chunks": [{"source": "skill", "snippet": "use X"}]}}},
         {"question_id": "q2", "question_text": "Q2?", "arms": {"baseline": {"answer": "b2"}, "skill:x": None}}],
        [{"question_id": "q1", "question_text": "Q1?", "retrieval_hit": True,
          "scores": {"baseline": {"aggregate": 60.0}, "skill:x": {"aggregate": 75.0}}},
         {"question_id": "q2", "question_text": "Q2?", "scores": {"baseline": {"aggregate": 50.0}, "skill:x": None}}],
    )


def test_an_arms_artifact_joins_answers_and_scores_by_question():
    run = records.read(_arms_run())
    assert (run.shape, run.baseline, run.treatments, run.judged) == ("arms", "baseline", ("skill:x",), True)
    q1, q2 = run.records
    assert q1.answer("skill:x").context == ["use X"]
    assert (q1.score("baseline"), q1.score("skill:x")) == (60.0, 75.0)
    assert q1.expected_skills == ("x",) and q1.retrieval_hit is True  # stamped evaluation wins
    assert q2.answer("skill:x") is None and q2.conditions == ("baseline", "skill:x")
    assert q2.score("skill:x") is None and q2.nulls == ("skill:x",)


def test_the_legacy_inline_score_is_read_from_aggregate_score():
    rec = records.record({"question_id": "q1", "arms": {
        "baseline": {"answer": "b", "evaluation": {"aggregate_score": 55}},
        "docs": {"answer": "d", "evaluation": {"aggregate_score": 65}},
    }})
    assert (rec.score("baseline"), rec.score("docs")) == (55, 65)


def test_expected_skill_is_read_by_presence():
    def label(**extra):
        return records.record({"question_id": "q1", "arms": {}, **extra}).expected_skills

    assert label() is None                          # unlabelled: not scored for selection
    assert label(expected_skill=None) == ()         # no skill should load
    assert label(expected_skill="a") == ("a",)
    assert label(expected_skill=["a", "b"]) == ("a", "b")


def test_section_reads_one_list_and_refuses_a_file_without_it():
    data = _arms_run()
    answers = records.read(data, section="answers")
    assert not answers.judged and [r.key for r in answers] == ["q1", "q2"]
    with pytest.raises(RecordError, match="no 'answers' list"):
        records.read(_evals(_eval("q1", 80, 60)), section="answers")
    assert records.read([_answer("q1")], section="answers").records[0].answer(CONTEXT).text


# ── errors ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("data, message", [
    ("nope", "expected a JSON object or a list"),
    ({"model": "m"}, "no 'answers' or 'evaluations' list"),
    ({"answers": {"q1": {}}}, "'answers' must be a list"),
    ({"answers": ["q1"]}, r"answers\[0\]: expected an object"),
    ({"answers": [{"question_text": "no id"}]}, r"answers\[0\]: missing question_id"),
    ({"answers": [{"question_id": 1.5}]}, r"question_id must be a string or an integer, got 1\.5"),
    ({"answers": [{"question_id": True}]}, "question_id must be a string or an integer, got True"),
    ({"answers": [{"question_id": "q1", "with_docs": "an answer"}]}, r"\.with_docs: expected an object or null"),
    ({"answers": [{"question_id": "q1", "arms": ["baseline"]}]}, "'arms' must be an object"),
    ({"answers": [{"question_id": "q1", "arms": {}, "expected_skill": 3}]}, "expected_skill must be"),
    ({"answers": [{"question_id": "q1", "arms": {}}, {"question_id": "q1", "arms": {}}],
      "evaluations": []}, "duplicate question_id 'q1' in answers"),
])
def test_malformed_input_is_a_record_error_naming_the_record(data, message):
    with pytest.raises(RecordError, match=message):
        records.read(data)


def test_load_names_the_file_on_every_failure(tmp_path):
    with pytest.raises(RecordError, match="file not found"):
        records.load(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RecordError, match=r"invalid JSON in .*bad\.json"):
        records.load(bad)
    good = tmp_path / "answers.json"
    good.write_text(json.dumps(_answers(_answer("q1"))), encoding="utf-8")
    assert [r.key for r in records.load(good)] == ["q1"]
    good.write_text(json.dumps({"answers": [{"question_text": "x"}]}), encoding="utf-8")
    with pytest.raises(RecordError, match=r"answers\.json answers\[0\]: missing question_id"):
        records.load(good)


def test_a_bare_list_is_read_by_shape():
    judged, answered = records.read([_eval("q1", 80.0, 60.0), _answer("q2")]).records
    assert judged.score(CONTEXT) == 80.0 and judged.answers == {}
    assert answered.answer(CONTEXT).text == "use parallel_for" and answered.scores == {}


def test_run_reading_edges(tmp_path):
    run = records.read(_arms_run())
    assert len(run) == 2 and [r.key for r in run] == ["q1", "q2"]
    assert records.read(run) is run  # a Run passes through, like a Record
    with pytest.raises(ValueError, match="section must be"):
        records.read(_arms_run(), section="scores")
    with pytest.raises(RecordError, match="expected a list of records"):
        records.records("q1")
    with pytest.raises(RecordError, match="duplicate question_id 'q1' in evaluations"):
        records.join([], [_eval("q1", 80, 60), _eval("q1", 70, 60)])
    with pytest.raises(RecordError, match="cannot read"):
        records.load(tmp_path)  # a directory


# ── join precedence, pinned (a change of any rule here fails a test) ─────


def test_a_join_takes_the_evaluations_score_over_an_inline_legacy_score_for_the_same_arm():
    answers = [{"question_id": "q1", "question_text": "Q1?", "arms": {
        "baseline": {"answer": "b", "evaluation": {"aggregate_score": 10}},
        "only_answers": {"answer": "o", "evaluation": {"aggregate_score": 33}},
        "skill:x": {"answer": "s"}}}]
    evals = [{"question_id": "q1", "question_text": "Q1?", "scores": {
        "baseline": {"aggregate": 60.0}, "skill:x": {"aggregate": 75.0}, "only_evaluation": {"aggregate": 5.0}}}]
    [q1] = records.join(answers, evals, source="run")
    # the evaluation's score wins for an arm both sides score; a one-sided score is kept
    assert q1.score("baseline") == 60.0
    assert q1.score("only_answers") == 33
    assert (q1.score("skill:x"), q1.score("only_evaluation")) == (75.0, 5.0)
    # conditions: the answers record's order first, then the evaluation-only ones
    assert q1.conditions == ("baseline", "only_answers", "skill:x", "only_evaluation")
    # answers come from the answers record only
    assert set(q1.answers) == {"baseline", "only_answers", "skill:x"}


def test_a_join_where_both_sides_carry_different_metadata():
    answers = [{**_answer("q1", docs=[_doc("ctx")], persona="dev-a", ground_truth_chunk="gt-a",
                          category="c-a", difficulty="easy", error="answer-error",
                          diagnosis={"kind": "answers"}, expected_skill="skill-a"),
                "question_text": "From the answers?", "shared": "answers", "only_a": 1}]
    evals = [_eval("q1", 80, 60, question_text="From the eval?", persona="dev-e", ground_truth_chunk="gt-e",
                   category="c-e", difficulty="hard", error="judge-error", diagnosis={"kind": "eval"},
                   expected_skill="skill-e", retrieval_hit=False, shared="eval", only_e=2)]
    [q1] = records.join(answers, evals, source="run")
    # the answers record wins for the question's text and metadata ...
    assert (q1.question_text, q1.category, q1.difficulty, q1.persona, q1.ground_truth) == (
        "From the answers?", "c-a", "easy", "dev-a", "gt-a")
    assert q1.error == "answer-error"
    assert q1.expected_skills == records.record(answers[0], kind="answers").expected_skills
    # ... the evaluation wins for what the judge produced: diagnosis and a stamped retrieval_hit
    assert q1.diagnosis == {"kind": "eval"}
    assert q1.retrieval_hit is False
    # raw: the answers record's keys win, keys from either side are kept
    assert (q1.raw["shared"], q1.raw["only_a"], q1.raw["only_e"]) == ("answers", 1, 2)


def test_a_join_keeps_an_answers_record_with_no_evaluation_and_appends_eval_only_ones_in_order():
    answers = [_answer("q1"), _answer("q2")]
    evals = [_eval("q4", 1, 1), _eval("q2", 70, 60), _eval("q3", 2, 2)]
    out = records.join(answers, evals, source="run")
    assert [r.key for r in out] == ["q1", "q2", "q4", "q3"]
    assert out[0].scores == {} and out[1].score(CONTEXT) == 70


def test_a_join_refuses_a_duplicate_answers_record():
    with pytest.raises(RecordError, match="duplicate question_id 'q1' in answers"):
        records.join([_answer("q1"), _answer("q1")], [], source="run")
