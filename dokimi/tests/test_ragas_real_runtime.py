# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""RagasEvaluator scores answers through the installed ragas, with only the models stubbed.

tests/test_eval_ragas.py fakes ragas's result frame, so it cannot see ragas rename a
column or demand a new one (0.3 did both, and every score was lost). Here the real
ragas runs in a fresh interpreter (see tests/test_ragas_runtime.py for why) with a
LangChain judge and embeddings that answer offline; the socket is closed to anything else.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_PROBE = r'''
import json
import socket
import sys


def _no_net(*a, **k):
    raise RuntimeError("network blocked in the ragas probe")


socket.socket.connect = _no_net

import langchain_anthropic
import langchain_openai
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


def _reply(prompt):
    """The judge's answer to one ragas prompt, told apart by its instruction."""
    task = prompt.split("Now perform the same with the following input")[-1]
    if "Break down each sentence" in prompt:
        return {"statements": ["s1", "s2", "s3", "s4"]}
    if "judge the faithfulness" in prompt:
        return {"statements": [{"statement": f"s{i}", "reason": "r", "verdict": v}
                               for i, v in enumerate([1, 1, 1, 0])]}
    if "noncommittal" in prompt:
        return {"question": "q", "noncommittal": int("BASELINE" in task)}
    if "verify if the context was useful" in prompt:
        return {"reason": "r", "verdict": int("USEFUL" in task)}
    if "attributed to the given context" in prompt:
        return {"classifications": [{"statement": f"s{i}", "reason": "r", "attributed": v}
                                    for i, v in enumerate([1, 0, 0, 0])]}
    raise AssertionError("a ragas prompt the stub judge does not know: " + prompt[:300])


class StubJudge(BaseChatModel):
    @property
    def _llm_type(self):
        return "stub"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        reply = json.dumps(_reply(" ".join(str(m.content) for m in messages)))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])


class StubEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text):
        return [1.0, 0.0]


langchain_anthropic.ChatAnthropic = lambda **kw: StubJudge()
langchain_openai.OpenAIEmbeddings = lambda **kw: StubEmbeddings()

from dokimi.eval.ragas_eval import RagasEvaluator

answers, metrics = json.loads(sys.argv[1]), json.loads(sys.argv[2])
result = RagasEvaluator(provider="anthropic", llm_model="stub", metrics=metrics).evaluate(answers)
print(json.dumps(result.to_dict()))
'''

ANSWER = {
    "question_id": "q1",
    "question_text": "How to use parallel_for?",
    "ground_truth_chunk": "tbb::parallel_for(range, body) runs body over subranges.",
    "with_docs": {
        "answer": "Use tbb::parallel_for(range, body).",
        # The relevant snippet second: context precision is then 0.5, not 1.0.
        "retrieved_docs": [{"snippet": "Unrelated release note."},
                           {"snippet": "USEFUL: tbb::parallel_for splits the range."}],
    },
    "without_docs": {"answer": "BASELINE: it parallelizes loops."},
}


def _require_ragas():
    if importlib.util.find_spec("ragas") is None:
        if os.environ.get("DOKIMI_REQUIRE_RAGAS") == "1":
            pytest.fail("DOKIMI_REQUIRE_RAGAS=1 but ragas is not installed")
        pytest.skip("the [ragas] extra is not installed")


def _evaluate(answers, metrics):
    _require_ragas()
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps(answers), json.dumps(metrics)],
        cwd=REPO,
        env={**os.environ, "RAGAS_DO_NOT_TRACK": "true", "OPENAI_API_KEY": "offline-dummy"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]
    # ragas logs a lost metric instead of raising; the probe's stderr shows why.
    return json.loads(proc.stdout.splitlines()[-1]), proc.stderr


def test_the_default_metrics_score_both_arms_on_the_installed_ragas():
    result, stderr = _evaluate([ANSWER], ["faithfulness", "answer_relevancy", "context_precision"])
    per_question = result["ragas_per_question"]
    assert list(per_question["with_docs"]) == ["q1"], stderr
    assert per_question["with_docs"]["q1"] == pytest.approx(
        {"faithfulness": 0.75, "answer_relevancy": 1.0, "context_precision": 0.5})
    assert per_question["without_docs"] == {"q1": {"answer_relevancy": 0.0}}, stderr
    assert result["ragas_summary"] == {
        "with_docs": {"faithfulness": 0.75, "answer_relevancy": 1.0, "context_precision": 0.5},
        "without_docs": {"answer_relevancy": 0.0},
    }


NO_TRUTH = {**ANSWER, "question_id": "q2", "ground_truth_chunk": None}


# ragas checks the required columns on the first row only: q2 first once erased every score.
@pytest.mark.parametrize("answers", [[ANSWER, NO_TRUTH], [NO_TRUTH, ANSWER]], ids=["truth-first", "truth-second"])
def test_context_recall_scores_the_ground_truth_and_skips_answers_without_one(answers):
    result, stderr = _evaluate(answers, ["faithfulness", "context_recall"])
    assert result["ragas_per_question"]["with_docs"] == {
        "q1": {"faithfulness": 0.75, "context_recall": 0.25},
        "q2": {"faithfulness": 0.75},
    }, stderr
    assert result["ragas_summary"]["with_docs"] == {"faithfulness": 0.75, "context_recall": 0.25}
    assert result["ragas_per_question"]["without_docs"] == {}


def test_context_recall_without_any_ground_truth_keeps_the_other_scores():
    result, stderr = _evaluate([NO_TRUTH], ["faithfulness", "context_recall"])
    assert result["ragas_per_question"]["with_docs"] == {"q2": {"faithfulness": 0.75}}, stderr
    assert result["ragas_summary"]["with_docs"] == {"faithfulness": 0.75}
