# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""--judge-placeholder reaches the judge of every entry point that takes it."""

import json
from types import SimpleNamespace

import pytest

from dokimi.cli import build_parser

GOOD = ('{"reasoning":"r","correctness":80,"completeness":80,"specificity":80,'
        '"code_quality":80,"actionability":80,"aggregate":80}')

#: A question whose retrieval missed: the with-docs answer is the no-retrieval placeholder.
ANSWERS = [{"question_id": "Q1", "question_text": "q", "question": "q",
            "with_docs": {"answer": "[FALLBACK: No relevant documentation found]",
                          "doc_source": "fallback_none", "retrieved_docs": []},
            "without_docs": {"answer": "without docs"}}]

JUDGE = ["--judge-provider", "amazon-bedrock", "--judge-model", "us.anthropic.x"]


@pytest.fixture
def requests(monkeypatch):
    import litellm

    sent = []

    def completion(**kw):
        sent.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=GOOD))],
                               usage=None)

    monkeypatch.setattr(litellm, "completion", completion)
    return sent


def _run(argv):
    args = build_parser().parse_args(argv)
    args.func(args)


def _eval_score(tmp_path, *flag):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(ANSWERS), encoding="utf-8")
    out = tmp_path / "eval.json"
    _run(["eval", "score", "pointwise", "--product", "L", "--answers", str(answers), "--output", str(out),
          "--concurrency", "1", *JUDGE, *flag])
    return json.loads(out.read_text(encoding="utf-8"))


def _evaluate(tmp_path, monkeypatch, *flag):
    from dokimi.orchestrator import EvaluationPipeline

    def run(self, **kw):
        self._evaluate_answers(ANSWERS)
        raise RuntimeError("stop after the judge step")

    monkeypatch.setattr(EvaluationPipeline, "run", run)
    with pytest.raises(SystemExit):
        _run(["evaluate", "--product", "L", "--description", "d", "--output-dir", str(tmp_path), *JUDGE, *flag])
    return json.loads((tmp_path / "eval" / "L.json").read_text(encoding="utf-8"))


def test_eval_score_leaves_the_placeholder_unjudged_by_default(tmp_path, requests):
    out = _eval_score(tmp_path)

    assert len(requests) == 1  # the baseline answer only
    assert out["evaluations"][0]["with_docs"] is None
    assert out["judge_placeholder"] is False


def test_eval_score_judge_placeholder_judges_it_and_says_so(tmp_path, requests):
    out = _eval_score(tmp_path, "--judge-placeholder")

    assert len(requests) == 2
    assert out["evaluations"][0]["with_docs"]["aggregate"] == 80
    assert out["judge_placeholder"] is True


def test_evaluate_leaves_the_placeholder_unjudged_by_default(tmp_path, requests, monkeypatch):
    out = _evaluate(tmp_path, monkeypatch)

    assert len(requests) == 1
    assert out["evaluations"][0]["with_docs"] is None
    assert out["judge_placeholder"] is False


def test_evaluate_judge_placeholder_judges_it_and_says_so(tmp_path, requests, monkeypatch):
    out = _evaluate(tmp_path, monkeypatch, "--judge-placeholder")

    assert len(requests) == 2
    assert out["evaluations"][0]["with_docs"]["aggregate"] == 80
    assert out["judge_placeholder"] is True
