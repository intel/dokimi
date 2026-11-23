# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An ``arms.v1`` run reads as ``answers.v1``/``eval.v1`` in the pipeline's readers (#228 step 4b).

The golden test runs both answering stacks on the parity fixture (``tests/answering_stack_parity.py``),
passes Stack A's ``arms.v1`` run through :func:`dokimi.eval.arms_adapter.to_binary`, and reads the result
and Stack P's own artifacts with every reader of those files: the record reader, the trust gate, ``report
generate``, ``report eval``, the dashboard, RAGAS, grounding, ``baseline`` and ``report model-compare``.
On the five questions the stacks answer alike, every reader gives the same result, except the rows of
:data:`PINNED`. The sixth, q-empty, is #228 D2 (no docs found): :data:`EMPTY_D2` pins how it differs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
from unittest.mock import Mock, patch

import pytest

import dokimi.eval.arm_runner as arm_runner_mod
from dokimi.artifacts import save_artifact
from dokimi.cli import build_parser
from dokimi.eval.arms_adapter import to_binary
from dokimi.records import RecordError, is_knowledge_run, load, read
from dokimi.report.stop_note import stop_note
from tests.answering_stack_parity import QUESTIONS, run_stack_a, run_stack_p

STEM = "fixture-lib.json"
EMPTY = "q-empty"

# (difference, key, value on Stack P's own files, value on the adapted arms run), on the five questions
# both stacks answer alike. Every other key of _observe must be equal on both.
PINNED: Tuple[Tuple[str, str, Any, Any], ...] = (
    # The 200-character record snippet: the answerer cuts with "...", the arm runner with "…".
    ("snippet", "records.snippet_ends", ("...",), ("…",)),
    ("snippet", "ragas.context_ends", ("...",), ("…",)),
    # The pipeline's run_metadata names the question model and the question set; an arms run records
    # neither. The adapter adds the run_intent the arms run declares (paired: a baseline and one more arm).
    (
        "absent",
        "report_eval.run_metadata",
        ("answer_model", "answer_provider", "evaluator_independence_warning", "judge_model", "judge_provider",
         "question_model", "question_provider", "question_set_hash"),
        ("answer_model", "answer_provider", "evaluator_independence_warning", "judge_model", "judge_provider",
         "run_intent"),
    ),
)

# q-empty alone (#228 D2): Stack P judges no with-docs answer (the placeholder); Stack A answers with the
# plain prompt and judges it, so its delta is 0. Both record that no document reached the prompt.
EMPTY_D2: Tuple[Tuple[str, Any, Any], ...] = (
    ("with_docs.answer", "[FALLBACK: No relevant documentation found]", "Call parallel_for over a blocked_range."),
    ("retrieval_hit", False, False),
    ("scores", (None, 60.0), (60.0, 60.0)),
    ("delta", None, 0.0),
    ("diagnosis", "empty_retrieval", "docs_helped"),
    ("dashboard.unjudged_miss", True, False),
)


def _stacks(tmp_path: Path, provider_calls) -> Tuple[Path, Path, Dict[str, Any]]:
    """Both stacks on the fixture; ``(P's run dir, the adapted run's dir, the arms run)``."""
    with pytest.MonkeyPatch.context() as mp:
        run_stack_p(mp, tmp_path / "p", provider_calls)
    with pytest.MonkeyPatch.context() as mp:
        arms = run_stack_a(mp, tmp_path / "a", provider_calls).artifact
    answers, evaluation = to_binary(arms)
    assert evaluation is not None
    # save_artifact validates each payload against its schema.
    save_artifact("answers", answers, tmp_path / "a" / "answers" / STEM)
    save_artifact("eval", evaluation, tmp_path / "a" / "eval" / STEM)
    return tmp_path / "p", tmp_path / "a", arms


def _subset(run: Path, dest: Path, keep: Iterable[str]) -> Path:
    """A copy of *run*'s answers and eval files holding only the questions in *keep*."""
    keep = set(keep)
    for kind, key in (("answers", "answers"), ("eval", "evaluations")):
        data = json.loads((run / kind / STEM).read_text(encoding="utf-8"))
        data[key] = [r for r in data[key] if r["question_id"] in keep]
        (dest / kind).mkdir(parents=True)
        (dest / kind / STEM).write_text(json.dumps(data), encoding="utf-8")
    return dest


def _ragas_rows(answers) -> list:
    """The rows RAGAS is handed (its runtime and models stubbed): what it reads of an answers file."""
    from dokimi.eval.ragas_eval import RagasEvaluator

    metrics = [Mock(llm=None) for _ in ("faithfulness", "answer_relevancy")]
    for metric, name in zip(metrics, ("faithfulness", "answer_relevancy"), strict=True):
        metric.name = name
    with patch.object(RagasEvaluator, "_build_ragas_llm", return_value=Mock()), \
         patch.object(RagasEvaluator, "_build_metrics", return_value=metrics), \
         patch.object(RagasEvaluator, "_build_ragas_embeddings", return_value=None):
        evaluator = RagasEvaluator(metrics=["faithfulness", "answer_relevancy"])
    dataset = Mock()
    with patch("dokimi.eval.ragas_eval._import_ragas_runtime", return_value=(dataset, Mock(side_effect=ValueError))):
        evaluator.evaluate(answers)
    return [call.args[0] for call in dataset.from_list.call_args_list]


def _ends(texts: Iterable[str]) -> Tuple[str, ...]:
    return tuple(sorted({"…" if t.endswith("…") else "..." if t.endswith("...") else "whole" for t in texts}))


def _observe(run: Path) -> Dict[str, Any]:
    """What each reader makes of the answers and eval files in *run*."""
    from dokimi.commands.baseline import _compute_summary
    from dokimi.dashboard.aggregator import ResultsAggregator
    from dokimi.eval.grounding import evaluate_grounding
    from dokimi.gate.trust_gate import evaluate_trust
    from dokimi.report.eval_report import significance_test
    from dokimi.report.generator import ReportGenerator
    from dokimi.report.model_compare import extract_scores
    from dokimi.report.single_condition_report import write_eval_report

    eval_path = run / "eval" / STEM
    eval_data = json.loads(eval_path.read_text(encoding="utf-8"))
    answers, evals = load(run / "answers" / STEM), load(eval_path)
    docs = [d.text for r in answers if (a := r.answer("with_docs")) for d in a.docs]
    rows = _ragas_rows(answers.records)
    report = json.loads(ReportGenerator().generate_report(eval_data, {"questions": QUESTIONS}, output_format="json"))
    write_eval_report(str(eval_path), str(run / "report.md"), qa_json_out=str(run / "qa.json"))
    qa = json.loads((run / "qa.json").read_text(encoding="utf-8"))
    [snap] = ResultsAggregator(run).aggregate().products
    return {
        "records.shape": (answers.shape, evals.shape, answers.conditions, evals.conditions),
        "records.knowledge_run": (is_knowledge_run(answers.records, answers.meta),
                                  is_knowledge_run(evals.records, evals.meta)),
        "records.answers": [(r.key, r.question_text, r.category, r.difficulty, r.ground_truth,
                             *((a.text, a.doc_source, a.error, [(d.source, d.relevance) for d in a.docs])
                               for a in (r.answer("with_docs"), r.answer("without_docs")) if a))
                            for r in answers],
        "records.retrieval_hit": [(r.key, r.retrieval_hit) for r in answers],
        "records.snippet_ends": _ends(docs),
        "records.scores": [(r.key, r.score("with_docs"), r.score("without_docs"), r.delta, r.error,
                            r.retrieval_hit, (r.diagnosis or {}).get("label")) for r in evals],
        "trust": evaluate_trust(eval_data["evaluations"]).as_dict(),
        "generate": {k: report[k] for k in ("stats", "top_with_docs", "bottom_with_docs", "top_deltas",
                                            "bottom_deltas", "clusters", "sources_summary", "trust", "warnings")},
        "generate.failure_counts": report["failure_analysis"]["counts"],
        "report_eval.md": [line for line in (run / "report.md").read_text(encoding="utf-8").splitlines()
                           if not line.startswith("_Generated")],
        "report_eval.qa_records": qa["records"],
        "report_eval.run_metadata": tuple(sorted(qa["run_metadata"])),
        "dashboard": (snap.product, bool(snap.evaluated_at), snap.judge_model, snap.total_questions, snap.avg_with_docs,
                      snap.avg_without_docs, snap.avg_delta, snap.doc_health, snap.status, snap.by_source),
        "dashboard.questions": [vars(q) for q in snap.questions],
        "ragas.rows": [[{**row, "retrieved_contexts": len(row.get("retrieved_contexts", ()))} for row in batch]
                       for batch in rows],
        "ragas.context_ends": _ends(c for batch in rows for row in batch for c in row.get("retrieved_contexts", ())),
        "grounding": evaluate_grounding(answers.records),
        "baseline.summary": _compute_summary(eval_data),
        "significance": significance_test(eval_data["evaluations"]),
        # model-compare reads arms files: a binary eval file gives it no rows (see the test below).
        "model_compare.rows": extract_scores(eval_data),
    }


def test_every_reader_reads_the_adapted_run_as_the_pipelines_own(tmp_path, provider_calls):
    p_run, a_run, _ = _stacks(tmp_path, provider_calls)
    agree = [q["id"] for q in QUESTIONS if q["id"] != EMPTY]
    p = _observe(_subset(p_run, tmp_path / "p5", agree))
    a = _observe(_subset(a_run, tmp_path / "a5", agree))
    pinned = {key: (pv, av) for _, key, pv, av in PINNED}
    assert set(pinned) <= set(p), set(pinned) - set(p)
    mismatches = [f"{key}: pinned {pinned.get(key)!r}, got P {p[key]!r}, A {a[key]!r}"
                  for key in p if (p[key], a[key]) != pinned.get(key, (p[key], p[key]))]
    assert not mismatches, "\n".join(mismatches)
    assert p["records.scores"][0][1:4] == (80.0, 60.0, 20.0)  # the comparison is over judged scores


def test_q_empty_differs_only_as_parity_d2_pins_it(tmp_path, provider_calls):
    p_run, a_run, _ = _stacks(tmp_path, provider_calls)
    got = {}
    for side, run in (("P", p_run), ("A", a_run)):
        subset = _subset(run, tmp_path / f"{side}-empty", [EMPTY])
        seen = _observe(subset)
        [answer] = load(subset / "answers" / STEM)
        [(_, w, wo, delta, _, hit, label)] = seen["records.scores"]
        [q] = seen["dashboard.questions"]
        got[side] = {"with_docs.answer": answer.answer("with_docs").text, "retrieval_hit": hit, "scores": (w, wo),
                     "delta": delta, "diagnosis": label, "dashboard.unjudged_miss": q["unjudged_miss"]}
    assert [(k, got["P"][k], got["A"][k]) for k, *_ in EMPTY_D2] == list(EMPTY_D2)


def test_model_compare_reads_the_arms_run_as_the_adapted_eval(tmp_path, provider_calls):
    # model-compare takes the arms file itself (extract_scores needs its baseline_arm); its rows are
    # the adapted eval's scores, question by question.
    from dokimi.report.model_compare import extract_scores

    _, a_run, arms = _stacks(tmp_path, provider_calls)
    rows = [(r["question_id"], r["with_docs"], r["without_docs"], r["delta"]) for r in extract_scores(arms)]
    evals = [(r.key, r.score("with_docs"), r.score("without_docs"), r.delta) for r in load(a_run / "eval" / STEM)]
    assert rows == evals
    assert len(rows) == 6


# ── Shapes the fixture does not reach ────────────────────────────────────────

def _arms(*records: Dict[str, Any], arms=("baseline", "docs"), evaluations=None, **meta) -> Dict[str, Any]:
    out = {"schema_version": "arms.v1", "model": "m", "provider": "openai", "arms": list(arms),
           "baseline_arm": "baseline", "answers": list(records), **meta}
    if evaluations is not None:
        out.update(evaluations=evaluations, judge_model="j", judge_provider="openai")
    return out


def _cell(answer="an answer", chunks=None) -> Dict[str, Any]:
    return {"answer": answer, "context_chunks": chunks or [], "metadata": {"arm_kind": "docs"}}


def _question(qid="q1", **arms) -> Dict[str, Any]:
    return {"question_id": qid, "question_text": "How?", "arms": arms}


def _scores(qid="q1", **scores) -> Dict[str, Any]:
    return {"question_id": qid, "question_text": "How?", "scores": scores}


def test_a_failed_cell_is_a_null_condition_named_in_the_error_and_judged_nowhere():
    # As the answerer writes a failed condition: null, with the record's error (eval/answerer.py).
    failed = {"error": "generation_failed: boom", "answer": None, "metadata": {"arm_kind": "docs"}}
    answers, evaluation = to_binary(_arms(
        _question(baseline=_cell(), docs=failed),
        evaluations=[_scores(baseline={"aggregate": 60.0}, docs={"error": "generation_failed: boom"})]))
    [a], [e] = answers["answers"], evaluation["evaluations"]
    assert (a["with_docs"], a["error"]) == (None, "docs: generation_failed: boom")
    assert (e["with_docs"], e["delta"], e["diagnosis"]["label"]) == (None, None, "insufficient_data")
    assert "error" not in e  # the answer failed, not the judge: the answers file says so
    assert e["retrieval_hit"] is None  # no context answer: unrecorded, as for the pipeline's failed question


def test_a_judge_failure_on_an_answer_is_the_eval_records_error():
    # As the judge writes a failed judgement: no score, the record's error (eval/judge.py evaluate_answers).
    answers, evaluation = to_binary(_arms(
        _question(baseline=_cell(), docs=_cell(chunks=[{"source": "a.md", "snippet": "text"}])),
        evaluations=[_scores(baseline={"aggregate": 60.0}, docs={"error": "judge broke"})]))
    [e] = evaluation["evaluations"]
    assert (e["with_docs"], e["delta"], e["error"], e["retrieval_hit"]) == (None, None, "docs: judge broke", True)
    assert answers["answers"][0]["with_docs"]["doc_source"] == "a.md"


@pytest.mark.parametrize("chunks, docs", [
    (["text", {}, {"source": "a.md", "snippet": "s"}], [{"source": "a.md", "snippet": "s"}]),
    ({"a": 1}, []),
])
def test_retrieved_docs_are_the_chunks_the_record_reader_reads(chunks, docs):
    # records._answer keeps the non-empty objects of a list; a cell of another shape is not a crash.
    answers, _ = to_binary(_arms(_question(baseline=_cell(), docs={**_cell(), "context_chunks": chunks})))
    with_docs = answers["answers"][0]["with_docs"]
    assert with_docs["retrieved_docs"] == docs
    assert with_docs.get("doc_source") == (docs[0]["source"] if docs else None)
    assert [d.source for d in read(answers).records[0].answer("with_docs").docs] == [d["source"] for d in docs]


def test_a_legacy_inline_evaluation_is_not_a_score_of_the_run():
    # records.arm_scores: an arms run's stored judge is its evaluations list, not arms[name].evaluation.
    legacy = {"evaluation": {"aggregate_score": 70}}
    _, evaluation = to_binary(_arms(_question(baseline={**_cell(), **legacy}, docs={**_cell(), **legacy}),
                                    evaluations=[]))
    assert evaluation is not None
    [e] = evaluation["evaluations"]
    assert (e["with_docs"], e["without_docs"], e["delta"]) == (None, None, None)  # was a KeyError on "aggregate"
    _, evaluation = to_binary(_arms(_question(baseline=_cell(), docs={**_cell(), **legacy}),
                                    evaluations=[_scores(baseline={"aggregate": 60.0})]))
    [e] = evaluation["evaluations"]
    assert (e["with_docs"], e["without_docs"]["aggregate"], e["delta"]) == (None, 60.0, None)


def test_a_baseline_only_run_reads_as_a_knowledge_run():
    # An arms file of the baseline arm alone is a knowledge run (records.is_knowledge_run).
    answers, evaluation = to_binary(_arms(
        _question(baseline=_cell()), arms=("baseline",), evaluations=[_scores(baseline={"aggregate": 70.0})]))
    run = read(answers)
    assert (answers["run_intent"], answers["answers"][0]["with_docs"]) == ("knowledge", None)
    assert is_knowledge_run(run.records, run.meta)
    assert evaluation["evaluations"][0]["diagnosis"]["label"] == "single_condition"


def test_method_version_is_carried_and_never_stamped():
    # #228 step 2: the stamp says which method answered; arms.v1 has none yet (parity row artifact.method_version).
    with_version = to_binary(_arms(_question(baseline=_cell(), docs=_cell()), evaluations=[], method_version=4))
    assert [part["method_version"] for part in with_version] == [4, 4]
    without = to_binary(_arms(_question(baseline=_cell(), docs=_cell()), evaluations=[]))
    assert all("method_version" not in part for part in without)


@pytest.mark.parametrize("data, message", [
    ({"schema_version": "answers.v1", "answers": []}, "not an arms.v1 artifact"),
    (_arms(_question(baseline=_cell(), docs=_cell(), skill=_cell()), arms=("baseline", "docs", "skill")),
     "hold the baseline arm and at most one other"),
    (_arms(_question(docs=_cell()), arms=("docs",)), "with baseline_arm 'baseline' do not fit"),
    # A profile arm retrieves nothing: as with_docs every question would read as a retrieval miss.
    (_arms(_question(baseline=_cell(), profile={**_cell(), "metadata": {"arm_kind": "profile"}}),
           arms=("baseline", "profile")), r"arm 'profile' \(arm_kind \['profile'\]\) is not a docs arm"),
    (_arms(_question(baseline=_cell(), docs={"answer": "a"})), r"arm 'docs' \(arm_kind unrecorded\) is not a docs arm"),
])
def test_a_run_with_no_binary_reading_is_refused(data, message):
    # answers.v1/eval.v1 have two conditions, with_docs and without_docs (AGENTS.md, "Persisted formats").
    with pytest.raises(RecordError, match=message):
        to_binary(data)


def test_a_stopped_run_keeps_its_stop_in_both_files():
    # A shortened run is not read as a whole one (#895): the report's stop note reads either file.
    stop = {"reason": "usage limit", "questions_requested": 4, "questions_recorded": 1}
    answers, evaluation = to_binary(_arms(_question(baseline=_cell(), docs=_cell()), evaluations=[],
                                          terminated=stop))
    assert evaluation is not None
    assert answers["terminated"] == evaluation["run_metadata"]["terminated"] == stop
    assert stop_note(evaluation)[0].startswith("> **Stopped early**: usage limit. 1 of 4 questions")


@pytest.mark.parametrize("kind", ["docs", "mcp_doc"])
def test_a_docs_or_mcp_arm_is_with_docs(kind):
    # The two kinds DocTreatment stamps (treatments/factory.py: docs:, mcp:).
    cell = {**_cell(chunks=[{"source": "a.md", "snippet": "s"}]), "metadata": {"arm_kind": kind}}
    answers, _ = to_binary(_arms(_question(baseline=_cell(), docs=cell)))
    assert read(answers).records[0].retrieval_hit is True


def _stopped() -> Dict[str, Any]:
    """The cells of a question a run-wide error stopped on its baseline: arm_stop records no arm_kind."""
    from dokimi.eval.arm_stop import stop_question

    cells: Dict[str, Any] = {}
    stop_question(cells, ["baseline", "docs"], "baseline", RuntimeError("usage limit"))
    return cells


def test_a_run_stopped_after_its_docs_arm_answered_converts_with_its_stop():
    # q1's docs cell names the arm; q2's stop cells name nothing and read as failed conditions.
    cells = _stopped()
    stop = {"reason": "usage limit", "questions_requested": 4, "questions_recorded": 2}
    answers, evaluation = to_binary(_arms(
        _question(baseline=_cell(), docs=_cell()), _question("q2", **cells), terminated=stop,
        evaluations=[_scores(baseline={"aggregate": 60.0}, docs={"aggregate": 80.0}),
                     _scores("q2", baseline={"error": cells["baseline"]["error"]}, docs={"error": cells["docs"]["error"]})]))
    a, e = answers["answers"][1], evaluation["evaluations"][1]
    assert answers["terminated"] == evaluation["run_metadata"]["terminated"] == stop
    assert (a["with_docs"], a["without_docs"]) == (None, None)
    assert a["error"] == ("docs: not_asked: the run stopped before this arm; "
                          "baseline: generation_failed: usage limit")
    assert (e["with_docs"], e["without_docs"], e["delta"], e["retrieval_hit"]) == (None, None, None, None)
    assert evaluation["evaluations"][0]["delta"] == 20.0


def test_an_arms_run_stopped_before_its_docs_arm_answered_is_refused(monkeypatch):
    # ArmRunner's stop: a usage limit on q1's baseline, so no cell says what the docs arm is.
    from dokimi import llm_cli
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.treatments.arms import BaselineTreatment, DocTreatment

    def usage_limit(**kwargs):
        raise llm_cli.CliCallError("usage limit exceeded", retryable=False, run_wide=True)

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", usage_limit)
    runner = ArmRunner([BaselineTreatment(), DocTreatment(None, reranker=Mock())], model="m", provider="openai")
    records = runner.run("lib", [{"id": "q1", "question": "How?"}], concurrency=1)
    with pytest.raises(RecordError, match="arm 'docs' records no arm_kind: the run stopped before the arm ran"):
        to_binary(runner.build_output("lib", records))


@pytest.mark.parametrize("data", [
    _arms(_question(**_stopped())),
    # The partial file of a run killed before its first question: no cell at all.
    {**_arms(arms=("baseline", "docs")), "partial": {"questions_requested": 4, "questions_recorded": 0}},
])
def test_a_run_with_no_cell_naming_its_arm_is_refused(data):
    with pytest.raises(RecordError, match="records no arm_kind: the run stopped before the arm ran"):
        to_binary(data)


def test_the_partial_file_of_a_crashed_run_reads_as_incomplete(tmp_path, monkeypatch):
    # docs/evaluating-treatments.md, "While it answers": a crash leaves <out stem>.partial.json, none judged.
    # Here a knowledge run (the baseline arm alone) crashes on q2, and q0, q1, q3 are kept.
    def fake(prompt, model, provider="openai", **kw):
        return "an answer", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    real_record = arm_runner_mod.question_record

    def record(library_name, q, qid, *a, **k):
        if qid == "q2":
            raise RuntimeError("boom")
        return real_record(library_name, q, qid, *a, **k)

    monkeypatch.setattr(arm_runner_mod, "llm_call_with_usage", fake)
    monkeypatch.setattr(arm_runner_mod, "question_record", record)
    questions = tmp_path / "q.json"
    questions.write_text(json.dumps([{"id": f"q{i}", "question": f"How do I do thing {i}?"} for i in range(4)]),
                         encoding="utf-8")
    args = build_parser().parse_args([
        "arms", "run", "--product", "onedal", "--questions", str(questions), "--arms", "baseline",
        "--concurrency", "1", "--out", str(tmp_path / "arms.json"), "--out-md", str(tmp_path / "arms.md")])
    with pytest.raises(RuntimeError, match="boom"):
        args.func(args)
    data = json.loads((tmp_path / "arms.partial.json").read_text(encoding="utf-8"))

    answers, evaluation = to_binary(data, source="arms.partial.json")
    assert evaluation is None  # nothing in it was judged
    assert answers["terminated"] == {"questions_requested": 4, "questions_recorded": 3}
    assert [a["question_id"] for a in answers["answers"]] == ["q0", "q1", "q3"]
    save_artifact("answers", answers, tmp_path / "answers.json")
    assert stop_note(answers)[0] == (
        "> **Stopped early**. 3 of 4 questions have a record, counting any the stop interrupted. "
        "Every number below covers only questions answered before the stop.")
    with pytest.raises(RecordError, match="is the partial file of a run that did not finish"):
        read(data)  # the arms file itself is still refused
