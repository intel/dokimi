# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The shapes the dashboard, the judge-context store, ``report compare`` and the arms report read.

Each of the four once read its ``answers`` or ``evaluations`` list off the artifact dict itself
(https://github.com/intel/dokimi/issues/229, P2-8); these tests pin what each one accepts and
refuses, the legacy shapes included, so moving them onto ``dokimi.records`` keeps it.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

import pytest

from dokimi.dashboard.aggregator import ResultsAggregator
from dokimi.eval.judge_context import JudgeContextStore
from dokimi.records import RecordError
from dokimi.report.arms_report import render_arms_report
from dokimi.report.model_compare import check_run_consistency, extract_scores, load_run
from dokimi.utils import InputError, digest_text

ARTIFACTS = Path(__file__).parent / "fixtures" / "artifacts"


# ── dashboard/aggregator.py ───────────────────────────────────────────────────

def _dashboard(tmp_path: Path, payload) -> list:
    path = tmp_path / "run" / "eval" / "demo.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ResultsAggregator(tmp_path).aggregate().products


def test_dashboard_reads_the_eval_fixture(tmp_path):
    (tmp_path / "eval").mkdir()
    shutil.copy(ARTIFACTS / "eval.v1.json", tmp_path / "eval" / "demo.json")
    [p] = ResultsAggregator(tmp_path).aggregate().products
    q = p.questions[0]
    assert (p.product, p.judge_model, len(p.questions)) == ("demo", "demo-judge", 1)
    assert (q.question_id, q.question, q.with_docs_score, q.without_docs_score, q.delta) == (
        "q1", "How do I install the library?", 80.0, 60.0, 20.0)


def test_dashboard_reads_a_panel_record_by_id_and_question_with_a_failed_baseline(tmp_path):
    [p] = _dashboard(tmp_path, {"library_name": "lib", "evaluations": [
        {"id": 7, "question": "Why?", "with_docs": {"aggregate": 90, "accuracy": 9}, "without_docs": None},
    ]})
    q = p.questions[0]
    assert (q.question_id, q.question, q.with_docs_score, q.without_docs_score) == (7, "Why?", 90.0, None)
    assert q.with_docs_dimensions == {"accuracy": 9}


def test_dashboard_gives_an_arms_evaluation_no_binary_scores(tmp_path):
    [p] = _dashboard(tmp_path, {"evaluations": [
        {"question_id": "q1", "scores": {"baseline": {"aggregate": 50}, "docs": {"aggregate": 70}}},
    ]})
    q = p.questions[0]
    assert (q.with_docs_score, q.without_docs_score, p.avg_with_docs) == (None, None, None)


@pytest.mark.parametrize("payload, warns", [
    ({"answers": [{"question_id": "q1", "with_docs": {"answer": "a"}}]}, False),
    ({"evaluations": []}, False),
    ([{"question_id": "q1", "with_docs": {"aggregate": 80}}], True),
    ({"evaluations": None}, True),
    ({"evaluations": {"q1": {"with_docs": {"aggregate": 80}}}}, True),
    ({"evaluations": ["q1"]}, True),
    ({"evaluations": [{"with_docs": {"aggregate": 80}}]}, True),
    ({"evaluations": [{"question_id": "q1", "with_docs": 80}]}, True),
], ids=["answers-file", "empty", "bare-list", "null", "object", "not-a-record", "no-id", "number-payload"])
def test_dashboard_skips_a_file_without_evaluation_records(tmp_path, caplog, payload, warns):
    with caplog.at_level(logging.WARNING, logger="dokimi.dashboard.aggregator"):
        assert _dashboard(tmp_path, payload) == []
    assert any("demo.json" in r.getMessage() for r in caplog.records) is warns


# ── eval/judge_context.py ─────────────────────────────────────────────────────

TEXT = "the skill body the arm was given"
DIGEST = digest_text(TEXT)


def _store(answers, caplog) -> JudgeContextStore:
    with caplog.at_level(logging.WARNING, logger="dokimi.eval.judge_context"):
        return JudgeContextStore.from_artifact({"judge_materials": {DIGEST: TEXT}, "answers": answers})


def _cited(answer: str = "a") -> dict:
    return {"answer": answer, "context_chunks": [], "judge_context_sha256": DIGEST}


def _warned(caplog) -> bool:
    return any("cite judge material" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("qid", ["q1", 0, 12])
def test_judge_context_serves_the_cited_material_by_question_id(caplog, qid):
    store = _store([{"question_id": qid, "arms": {"skill:x": _cited(), "baseline": {"answer": "b"}}}], caplog)
    assert (store.get(qid, "skill:x"), store.get(qid, "baseline"), _warned(caplog)) == (TEXT, None, False)


def test_judge_context_reads_a_legacy_record_keyed_by_id(caplog):
    store = _store([{"id": "q2", "arms": {"skill:x": _cited()}}], caplog)
    assert store.get("q2", "skill:x") == TEXT


def test_judge_context_keys_a_record_by_question_id_over_id_as_the_judges_look_it_up(caplog):
    store = _store([{"id": "x", "question_id": "Q1", "arms": {"skill:x": _cited()}}], caplog)
    assert (store.get("Q1", "skill:x"), store.get("x", "skill:x")) == (TEXT, None)


@pytest.mark.parametrize("digest", ["sha256:" + "0" * 64, "", 5])
def test_judge_context_drops_a_digest_it_cannot_verify_and_warns(caplog, digest):
    store = _store([{"question_id": "q1", "arms": {"skill:x": {"answer": "a", "judge_context_sha256": digest}}}],
                   caplog)
    assert (store.materials(), _warned(caplog)) == ({}, True)


def test_judge_context_keeps_the_good_records_beside_a_bad_digest(caplog):
    store = _store([{"question_id": "q1", "arms": {"skill:x": _cited()}},
                    {"question_id": "q2", "arms": {"skill:x": {"answer": "a", "judge_context_sha256": "x"}}}], caplog)
    assert (store.get("q1", "skill:x"), store.get("q2", "skill:x"), _warned(caplog)) == (TEXT, None, True)


@pytest.mark.parametrize("data", [
    {"answers": [{"question_id": "q1", "arms": {"skill:x": _cited()}}]},
    {"judge_materials": [TEXT], "answers": [{"question_id": "q1", "arms": {"skill:x": _cited()}}]},
    {"judge_materials": {DIGEST: TEXT}},
], ids=["no-materials", "materials-not-an-object", "no-answers"])
def test_judge_context_without_materials_or_answers_stores_nothing(data):
    assert JudgeContextStore.from_artifact(data).materials() == {}


@pytest.mark.parametrize("answers, message", [
    (None, "'answers' must be a list, got NoneType"),
    ({"q1": {}}, "'answers' must be a list, got dict"),
    (["q1"], r"answers\[0\]: expected an object, got str"),
    ([{"question_id": "q1", "arms": "skill:x"}], r"answers\[0\]: 'arms' must be an object"),
    ([{"question_id": "q1", "arms": {"skill:x": "a"}}], r"answers\[0\].arms.skill:x: expected an object or null"),
    ([{"arms": {"skill:x": _cited()}}], r"answers\[0\]: missing question_id"),
], ids=["null", "object", "not-an-object", "arms-not-an-object", "arm-not-an-object", "no-id"])
def test_judge_context_refuses_answers_the_records_reader_refuses(caplog, answers, message):
    with pytest.raises(RecordError, match=message):
        _store(answers, caplog)


@pytest.mark.parametrize("record", [
    {"question_id": "q1", "arms": {"skill:x": None}},
    {"question_id": "q1", "with_docs": {"answer": "a"}},
], ids=["null-arm", "binary"])
def test_judge_context_does_not_count_a_record_that_cites_nothing(caplog, record):
    store = _store([record, {"question_id": "q9", "arms": {"skill:x": _cited()}}], caplog)
    assert (store.get("q9", "skill:x"), _warned(caplog)) == (TEXT, False)


# ── report/model_compare.py ───────────────────────────────────────────────────

def _run_file(tmp_path: Path, payload) -> str:
    path = tmp_path / "run.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _arms_run(**extra) -> dict:
    return {"arms": ["baseline", "docs"], "baseline_arm": "baseline", "model": "m", **extra}


def test_report_compare_scores_an_arms_run_from_its_evaluations(tmp_path):
    data = load_run(_run_file(tmp_path, _arms_run(
        answers=[{"question_id": "q1", "arms": {"baseline": {"answer": "b"}, "docs": {"answer": "d"}}}],
        evaluations=[{"question_id": "q1", "question_text": "Q?", "difficulty": 2,
                      "scores": {"baseline": {"aggregate": 40}, "docs": {"aggregate": 70}}}])))
    [row] = extract_scores(data)
    assert (row["question_id"], row["difficulty"], row["without_docs"], row["with_docs"], row["delta"],
            row["treatment_arm"]) == ("q1", "medium", 40, 70, 30, "docs")


def test_report_compare_scores_a_legacy_run_from_inline_arm_evaluations(tmp_path):
    data = load_run(_run_file(tmp_path, _arms_run(answers=[{"question_id": 3, "arms": {
        "baseline": {"answer": "b", "evaluation": {"aggregate_score": 50}},
        "docs": {"answer": "d", "evaluation": {"aggregate_score": 65}}}}])))
    [row] = extract_scores(data)
    assert (row["question_id"], row["without_docs"], row["with_docs"], row["delta"]) == (3, 50, 65, 15)


@pytest.mark.parametrize("extra", [{"answers": []}, {"answers": [], "evaluations": []}], ids=["unjudged", "judged"])
def test_report_compare_reads_an_empty_section_as_no_records(tmp_path, extra):
    assert extract_scores(load_run(_run_file(tmp_path, _arms_run(**extra)))) == []


@pytest.mark.parametrize("payload, message", [
    ([{"question_id": "q1"}], "expected a JSON object"),
    (_arms_run(answers=None), "'answers' must be a list, got NoneType"),
    (_arms_run(answers={}), "'answers' must be a list, got dict"),
    (_arms_run(answers=[], evaluations=None), "'evaluations' must be a list, got NoneType"),
    (_arms_run(answers=[], evaluations={"q1": {}}), "'evaluations' must be a list, got dict"),
    (_arms_run(answers=[], evaluations=["q1"]), "expected an object"),
    (_arms_run(answers=[{"arms": {"docs": {"answer": "d"}}}]), "missing question_id"),
    (_arms_run(answers=[], evaluations=[{"question_id": "q1", "scores": ["docs"]}]), "'scores' must be an object"),
], ids=["bare-list", "null-answers", "empty-object-answers", "null-evaluations", "object-evaluations",
        "not-a-record", "no-id", "scores-not-an-object"])
def test_report_compare_refuses_a_malformed_run_naming_the_file(tmp_path, payload, message):
    path = _run_file(tmp_path, payload)
    with pytest.raises(InputError, match=message) as exc:
        load_run(path)
    assert path in str(exc.value)


def test_report_compare_warns_about_a_run_with_records_and_no_scored_treatment():
    unjudged = _arms_run(answers=[{"question_id": "q1", "arms": {"baseline": {"answer": "b"}}}])
    judged = _arms_run(answers=[], evaluations=[
        {"question_id": "q1", "scores": {"baseline": {"aggregate": 40}, "docs": {"aggregate": 70}}}])
    warnings = check_run_consistency([("a", unjudged), ("b", judged)], "g", pool_replicates=True)
    assert [w for w in warnings if "no scored non-baseline arm" in w] == [
        "Run 'a' has records but no scored non-baseline arm — it contributes a baseline column and no delta."]


def test_report_compare_does_not_warn_about_a_run_with_no_records():
    judged = _arms_run(answers=[], evaluations=[
        {"question_id": "q1", "scores": {"baseline": {"aggregate": 40}, "docs": {"aggregate": 70}}}])
    assert check_run_consistency([("a", _arms_run(answers=[])), ("b", judged)], "g", pool_replicates=True) == []


# ── report/arms_report.py ─────────────────────────────────────────────────────

def _arms_output(**extra) -> dict:
    return {"library_name": "lib", "model": "m", "provider": "p", "arms": ["baseline", "docs"],
            "baseline_arm": "baseline", "total_questions": 1, "answers": [], **extra}


def test_arms_report_lists_each_question_with_its_arm_scores():
    md = render_arms_report(_arms_output(evaluations=[
        {"question_id": "q1", "question_text": "How?", "scores": {"baseline": {"aggregate": 41.6}, "docs": None}},
        {"question_id": "q2", "question": "Panel?", "scores": {"baseline": {"aggregate": "high"},
                                                                "docs": {"aggregate": 90}}},
    ]))
    assert "## Per-question scores" in md
    assert "| How? | 42 | — |" in md
    assert "| Panel? | — | 90 |" in md


@pytest.mark.parametrize("extra", [{}, {"evaluations": []}], ids=["absent", "empty"])
def test_arms_report_without_evaluations_has_no_per_question_table(extra):
    assert "Per-question scores" not in render_arms_report(_arms_output(**extra))


def test_arms_report_counts_agentic_tool_use_from_old_answers():
    md = render_arms_report(_arms_output(arms=["baseline", "agent"], answers=[
        {"question_id": "q1", "arms": {"baseline": {"answer": "b"},
                                       "agent": {"agentic": True, "tool_call_count": 3, "iterations": 2}}},
        {"question_id": "q2", "arms": {"baseline": None, "agent": {"agentic": True, "tool_call_count": 0}}},
    ]))
    assert "| `agent` | 50% | 1.5 | — | 1.0 | 2 |" in md
    assert "`baseline` |" not in md.split("## Agentic tool use")[1]


@pytest.mark.parametrize("extra", [{"answers": None}, {"answers": {"q1": {}}}], ids=["null", "object"])
def test_arms_report_refuses_answers_that_are_not_a_list(extra):
    with pytest.raises(RecordError):
        render_arms_report(_arms_output(**extra))


@pytest.mark.parametrize("extra", [
    {"evaluations": None},
    {"evaluations": {}},
    {"evaluations": [{"scores": {"docs": {"aggregate": 1}}}]},
], ids=["null", "empty-object", "no-id"])
def test_arms_report_refuses_malformed_evaluations(extra):
    with pytest.raises(RecordError):
        render_arms_report(_arms_output(**extra))
