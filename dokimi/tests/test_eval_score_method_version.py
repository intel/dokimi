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
