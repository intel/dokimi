# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval score`` stamps the eval artifact with the method version of the answers it judged."""

import json
from types import SimpleNamespace

import pytest

from dokimi.cli import build_parser

GOOD = ('{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
        '"code_quality":80,"actionability":80,"aggregate":80}')

ANSWERS = [{"question_id": "Q1", "question_text": "q", "question": "q",
            "with_docs": {"answer": "an answer", "doc_source": "x", "retrieved_docs": ["d"]},
            "without_docs": {"answer": "without docs"}}]

JUDGE = ["--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.x"]


@pytest.fixture
def judge_calls(monkeypatch):
    import litellm

    sent = []

    def completion(**kw):
        sent.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD))],
                               usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    return sent


def _eval_score(tmp_path, answers_doc):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(answers_doc), encoding="utf-8")
    out = tmp_path / "eval.json"
    args = build_parser().parse_args(["eval", "score", "--product", "L", "--answers", str(answers),
                                      "--output", str(out), "--concurrency", "1", *JUDGE])
    args.func(args)
    return json.loads(out.read_text(encoding="utf-8"))


def test_unstamped_answers_are_legacy_so_the_eval_says_version_1(tmp_path, judge_calls):
    """Answers written before the stamp are the legacy method; re-judging them today does not make them version 2."""
    out = _eval_score(tmp_path, ANSWERS)

    assert len(judge_calls) == 2  # both arms judged: the stamp does not change what is judged
    assert out["method_version"] == 1


@pytest.mark.parametrize("version", [1, 2])
def test_stamped_answers_keep_their_method_version(tmp_path, judge_calls, version):
    out = _eval_score(tmp_path, {"method_version": version, "answers": ANSWERS})

    assert out["method_version"] == version


def _pipeline_judge_version(monkeypatch, tmp_path, answers, answers_file=None):
    """The method version the pipeline's judge is built with, for these answers."""
    from dokimi.eval.judge import Judge
    from dokimi.orchestrator.pipeline import EvaluationPipeline

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    seen = []

    def evaluate(self, product, records, concurrency=5):
        seen.append(self.answers_method_version)
        return []

    monkeypatch.setattr(Judge, "evaluate_answers", evaluate)
    monkeypatch.setattr(Judge, "save_evaluations", lambda self, evaluations, path: None)
    p = EvaluationPipeline.__new__(EvaluationPipeline)  # the attributes _evaluate_answers reads
    p.product, p.model, p.provider = "L", "gpt-x", "openai"
    p.judge_model, p.judge_provider, p.judge_temperature, p.judge_placeholder = "gpt-x", "openai", None, False
    p.answers_path, p.eval_path = answers_file, tmp_path / "eval.json"
    p._evaluate_answers(answers, concurrency=1)
    return seen[0]


def test_pipeline_judges_a_wrapped_version_1_artifact_as_version_1(monkeypatch, tmp_path):
    assert _pipeline_judge_version(monkeypatch, tmp_path, {"method_version": 1, "answers": ANSWERS}) == 1


def test_pipeline_judges_an_unstamped_answers_file_as_legacy_version_1(monkeypatch, tmp_path):
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps({"answers": ANSWERS}), encoding="utf-8")

    assert _pipeline_judge_version(monkeypatch, tmp_path, [], answers_file) == 1


def test_pipeline_judges_fresh_in_memory_answers_as_the_current_version(monkeypatch, tmp_path):
    from dokimi.defaults import ANSWERING_METHOD_VERSION

    assert _pipeline_judge_version(monkeypatch, tmp_path, ANSWERS) == ANSWERING_METHOD_VERSION


@pytest.mark.parametrize("bad", ["2", True, 3.5, [1], 0, -7])
def test_a_malformed_stamp_exits_2_before_any_judge_call(tmp_path, judge_calls, capsys, bad):
    """main ignored the field; writing it unchecked failed eval validation after paid calls."""
    with pytest.raises(SystemExit) as exc:
        _eval_score(tmp_path, {"method_version": bad, "answers": ANSWERS})

    assert exc.value.code == 2
    assert judge_calls == []
    assert not (tmp_path / "eval.json").exists()
    assert "method_version must be a positive integer" in capsys.readouterr().err


def test_a_null_stamp_is_legacy(tmp_path, judge_calls):
    assert _eval_score(tmp_path, {"method_version": None, "answers": ANSWERS})["method_version"] == 1


def test_pipeline_refuses_an_answers_file_with_a_malformed_stamp(monkeypatch, tmp_path):
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps({"method_version": "2", "answers": ANSWERS}), encoding="utf-8")

    with pytest.raises(ValueError, match="method_version must be a positive integer"):
        _pipeline_judge_version(monkeypatch, tmp_path, [], answers_file)
