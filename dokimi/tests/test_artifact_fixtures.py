# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The schema-checked fixture helper, and the contract it relies on.

``artifact_fixture`` only helps if the schemas declare every key the producers write; the
writer tests below run the real answerer and judge (LLM mocked) and hold their output to the
same closed-world check, so a producer that starts writing a new key fails here until its
schema documents it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from dokimi.artifacts import validate_artifact
from tests.artifact_fixtures import artifact_fixture
from tests.test_artifacts import PIPELINE_PAYLOADS

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "artifacts"


@pytest.mark.parametrize("kind", ["answers", "eval"])
def test_committed_and_inline_fixtures_are_producer_shaped(kind):
    committed = json.loads((FIXTURES / f"{kind}.v1.json").read_text(encoding="utf-8"))
    assert artifact_fixture(kind, committed) == committed
    artifact_fixture(kind, PIPELINE_PAYLOADS[kind])


def _panel_style_answers():
    """The shape tests/test_panel.py fed the panel judge: the reader's guess, not the writer's."""
    return {"answers": [{
        "question_id": "q0",
        "question": "Question 0?",
        "with_docs": {"answer": "Answer 0 with context.", "retrieved_docs": []},
        "without_docs": {"answer": "Answer 0 without context."},
    }]}


def test_the_open_schema_accepts_the_panel_style_shape():
    # Why write-time validation alone does not catch this class: answers.v1 is open, and
    # question_text is optional, so an answers file keyed `question` is schema-valid.
    validate_artifact("answers", _panel_style_answers())


def test_the_fixture_helper_rejects_the_panel_style_shape():
    with pytest.raises(AssertionError, match=r"answers/0/question: not declared .*question_text"):
        artifact_fixture("answers", _panel_style_answers())


def test_the_fixture_helper_rejects_a_schema_violation():
    with pytest.raises(Exception, match="question_id"):
        artifact_fixture("eval", {"evaluations": [{"question_text": "Q?"}]})


def test_objects_without_declared_properties_are_not_checked():
    # with_docs / without_docs carry no `properties`: the helper cannot see inside them (the
    # retrieved_docs snippet/content half of the panel bug stays out of its reach).
    inner = {"retrieved_docs": [{"content": "x"}], "no_producer_writes_this": 1}
    fixture = artifact_fixture("answers", {"answers": [{"question_id": "q", "with_docs": inner}]})
    assert fixture["answers"][0]["with_docs"] == inner


def _llm(prompt, **_kw):
    if "Why?" in prompt:
        raise RuntimeError("provider down")
    return "an answer", {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


QUESTIONS = [
    {"id": "q1", "text": "How?", "category": "api", "difficulty": "easy", "persona": "dev",
     "ground_truth_chunk": "parallel_for splits a range.", "question_source": "chunk"},
    {"id": "q2", "text": "Why?"},
]


def _answers_file(tmp_path):
    from dokimi.eval.answerer import Answerer

    client = Mock()
    client.get_library_docs.return_value = [{"content": "doc text", "source": "s"}]
    out = tmp_path / "answers.json"
    with patch("dokimi.eval.answerer.llm_call_with_usage", side_effect=_llm), \
         patch("dokimi.eval.answerer.LANGCHAIN_AVAILABLE", True):
        answerer = Answerer(mcp_client=client)
        answers = answerer.generate_answers("lib", "org/lib", QUESTIONS, output_path=out)
        answerer.save_answers(answers, out, question_set_hash="abc123")
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_answerer_writes_only_declared_keys(tmp_path):
    data = _answers_file(tmp_path)
    assert next(iter(data)) == "schema_version"  # the stamp still leads the document
    assert any(a.get("error") for a in data["answers"])  # the failure record is covered too
    artifact_fixture("answers", data)


def test_the_judge_writes_only_declared_keys(tmp_path):
    from dokimi.eval.judge import Judge

    scores = dict.fromkeys(("correctness", "completeness", "specificity", "code_quality",
                            "actionability", "factual_grounding"), 80)
    llm = Mock()
    llm.invoke.return_value = Mock(content=json.dumps({**scores, "aggregate": 80, "reasoning": {}}))
    answers = _answers_file(tmp_path)["answers"]
    out = tmp_path / "eval.json"
    with patch("dokimi.eval.judge.ChatAnthropic", Mock(return_value=llm), create=True), \
         patch("dokimi.eval.judge.LANGCHAIN_AVAILABLE", True):
        judge = Judge(run_metadata={"note": "test"})
        real = judge._evaluate_answer_pair

        def flaky(library_name, answer):
            if answer.question_id == "q2":
                raise RuntimeError("judge down")
            return real(library_name, answer)

        with patch.object(judge, "_evaluate_answer_pair", side_effect=flaky):
            judge.save_evaluations(judge.evaluate_answers("lib", answers, output_path=out), out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {e["question_id"] for e in data["evaluations"] if e.get("error")} == {"q2"}
    artifact_fixture("eval", data)
