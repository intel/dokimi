# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Helpers for the answering-stack characterization (#228 step 1).

Two stacks answer a question with an LLM and judge the answer:

* Stack P: :class:`dokimi.orchestrator.pipeline.EvaluationPipeline` driving
  :class:`dokimi.eval.answerer.Answerer`.
* Stack A: :class:`dokimi.eval.arm_runner.ArmRunner` with a
  :class:`dokimi.treatments.arms.BaselineTreatment` arm and a
  :class:`dokimi.treatments.arms.DocTreatment` arm.

These helpers run both stacks on one fixture and read back what each one sent
and recorded. Nothing here changes behaviour: the doc source, the reranker and
the clock are the only things replaced, and the provider seam is the real
``dokimi.llm`` adapter behind the ``provider_calls`` ledger.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dokimi import llm

PRODUCT = "fixture-lib"
PRODUCT_KEY = "fixture-lib"
LIBRARY_ID = "fixture/lib"  # both stacks are given this id, so only the request differs
REGISTRY_ID = "registry/lib"  # what products.yaml says for PRODUCT in the D8 check
ANSWER_MODEL = "fixture-answer-model"
ANSWER_PROVIDER = "openai"
JUDGE_MODEL = "fixture-judge-model"
JUDGE_PROVIDER = "openai"
TOP_K = 3  # the value the pipeline defaults to (defaults.DEFAULT_RETRIEVAL_TOP_K)
DESCRIPTION = "Fixture library for the answering-stack characterization."

# The fixture. One question per case in #228 step 1 (docs found, no docs,
# reranker raises, ground-truth chunk), plus two more docs-found questions so
# the paired test (stats.MIN_PAIRS_FOR_TEST = 6) runs on both stacks.
EMPTY_QUERY = "What does the arena do when it is empty?"
RERANK_QUERY = "Which scheduler owns the worker threads?"
DOCS_QUERY = "How does parallel_for split a blocked range?"
GROUND_TRUTH_QUERY = "Which header declares parallel_for?"
GROUND_TRUTH_CHUNK = "parallel_for is declared in oneapi/tbb/parallel_for.h."

QUESTIONS: List[Dict[str, Any]] = [
    {"id": "q-docs", "question": DOCS_QUERY, "category": "api", "difficulty": "easy"},
    {"id": "q-empty", "question": EMPTY_QUERY, "category": "api", "difficulty": "easy"},
    {"id": "q-rerank", "question": RERANK_QUERY, "category": "api", "difficulty": "easy"},
    {
        "id": "q-gt",
        "question": GROUND_TRUTH_QUERY,
        "category": "api",
        "difficulty": "easy",
        "ground_truth_chunk": GROUND_TRUTH_CHUNK,
    },
    {
        "id": "q-docs-2",
        "question": "How do I cancel a task group?",
        "category": "api",
        "difficulty": "easy",
    },
    {
        "id": "q-docs-3",
        "question": "What is the default grain size?",
        "category": "api",
        "difficulty": "easy",
    },
]

# Context7Client.get_library_docs defaults. The recording client applies them
# so a request that leaves a keyword out is visible as its effective value.
CONTEXT7_DEFAULTS = {"max_results": 5, "max_tokens": 8000}
RESOLVED_BY_CLIENT = "client/resolved"

# Prompt markers. Stack P's with-docs prompt has "**Relevant documentation:**";
# Stack A's has "**Relevant context:**". The plain prompt is what the
# baseline arm sends, and what Stack A's docs arm sends when it has no docs.
P_DOCS_MARKER = "**Relevant documentation:**"
A_DOCS_MARKER = "**Relevant context:**"
JUDGE_PURPOSE = "judge"


def _doc_text(query: str, index: int) -> str:
    # Longer than 200 characters, so the report snippet cuts it.
    return (
        f"Passage {index} for '{query}': parallel_for splits the blocked range "
        f"into subranges and runs the body on the worker threads. "
    ) * 3


class RecordingDocClient:
    """A doc source that records each retrieval request, as Context7 would take it.

    ``get_library_docs`` takes ``**kwargs`` and applies :data:`CONTEXT7_DEFAULTS`,
    so ``passed`` says what the caller sent and ``max_results``/``max_tokens``
    the effective values the server would have seen. An empty query returns no docs.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def resolve_library_id(self, product: str) -> str:
        return RESOLVED_BY_CLIENT

    def get_library_docs(self, library_id: str, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        effective = {**CONTEXT7_DEFAULTS, **kwargs}
        self.calls.append(
            {
                "library_id": library_id,
                "query": query,
                "passed": tuple(sorted(kwargs)),
                "max_results": effective["max_results"],
                "max_tokens": effective["max_tokens"],
            }
        )
        if query == EMPTY_QUERY:
            return []
        return [{"source": f"doc-{i}.md", "content": _doc_text(query, i)} for i in range(4)]


class StubReranker:
    """Scores docs in order; raises for the questions in *raise_for* (the rerank failure case)."""

    def __init__(self, threshold: float = 0.3, raise_for: Sequence[str] = (RERANK_QUERY,)) -> None:
        self.threshold = threshold
        self.raise_for = tuple(raise_for)
        self.calls: List[str] = []

    def rerank(self, question: str, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        self.calls.append(question)
        if question in self.raise_for:
            raise RuntimeError("reranker unavailable")
        return [dict(d, relevance_score=round(0.9 - 0.1 * i, 2)) for i, d in enumerate(docs)]


class FrozenClock:
    """Stands in for the ``time`` module inside the answering modules."""

    NOW = 1_700_000_000.0

    def time(self) -> float:
        return self.NOW

    def sleep(self, seconds: float) -> None:  # pragma: no cover - the answering path never sleeps
        raise AssertionError("the answering path must not sleep")


@dataclass
class Observed:
    """What one stack did on the fixture: its outputs, the judge's inputs and the provider calls."""

    stack: str
    client: RecordingDocClient
    reranker: StubReranker
    answers: List[Dict[str, Any]] = field(default_factory=list)
    evaluations: List[Dict[str, Any]] = field(default_factory=list)
    judge_inputs: List[Dict[str, Any]] = field(default_factory=list)
    artifact: Dict[str, Any] = field(default_factory=dict)
    # (ProviderCall, Request) pairs for this run only, in send order.
    requests: List[Tuple[Any, llm.Request]] = field(default_factory=list)


def install_judge_spy(monkeypatch) -> List[Dict[str, Any]]:
    """Record the keyword inputs of every ``Judge._judge_answer`` call, then run the real judge."""
    from dokimi.eval import judge as judge_mod

    original = judge_mod.Judge._judge_answer
    seen: List[Dict[str, Any]] = []

    def spy(self, **kwargs):
        seen.append(dict(kwargs))
        return original(self, **kwargs)

    monkeypatch.setattr(judge_mod.Judge, "_judge_answer", spy)
    return seen


JUDGE_SCORE_WITH_DOCS = 80
JUDGE_SCORE_NO_DOCS = 60


def _judge_reply(score: int) -> str:
    keys = (
        "correctness",
        "completeness",
        "specificity",
        "code_quality",
        "actionability",
        "factual_grounding",
    )
    return json.dumps({"reasoning": "r", "aggregate": 0, **{k: score for k in keys}})


def answer_with_canned_reply(provider_calls) -> None:
    """Replies for the fixture: every answer gets one fixed reply, and the judge scores by context.

    Scope: ``provider_calls`` is a fresh fixture per test, so the replaced ``respond`` lasts for that test only.

    The judge gives 80 when it sees real docs and 60 when the context is a
    "(No documentation ...)" sentinel, so each question's paired delta is
    non-zero where docs were found and zero where none were. Without this
    every delta is 0 and the paired test cannot tell two stacks apart.
    The A prompts are not in the canned table; they get the answer reply.
    Idempotent, so the wrapper is not stacked across stack runs. It is set on the
    fake directly rather than through MonkeyPatch, which is safe only because the
    fixture is per test.
    """
    from tests.provider_calls import ANSWER_REPLY, purpose_of

    fake = provider_calls.fake
    if getattr(fake, "_parity_replies", False):
        return
    canned = fake.respond

    def respond(request: llm.Request) -> str:
        text = llm.prompt_text(request)
        purpose = purpose_of(text)
        if purpose == "other":
            return ANSWER_REPLY
        if purpose == "judge":
            return _judge_reply(
                JUDGE_SCORE_NO_DOCS if "(No documentation" in text else JUDGE_SCORE_WITH_DOCS
            )
        return canned(request)

    fake.respond = respond
    fake._parity_replies = True


def install_common(monkeypatch, client: RecordingDocClient, reranker: StubReranker) -> None:
    """Replace the doc source, the reranker class and the answering modules' clock; nothing else."""
    from dokimi.eval import answerer as answerer_mod
    from dokimi.eval import arm_runner as arm_runner_mod
    from dokimi.orchestrator import pipeline as pipeline_mod

    clock = FrozenClock()
    monkeypatch.setattr(pipeline_mod, "create_doc_source_client", lambda kind: client)
    monkeypatch.setattr(answerer_mod, "SENTENCE_TRANSFORMERS_AVAILABLE", False)
    monkeypatch.setattr(answerer_mod, "SimpleReranker", lambda threshold: reranker)
    monkeypatch.setattr(answerer_mod, "time", clock)
    monkeypatch.setattr(arm_runner_mod, "time", clock)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")


def _requests_since(provider_calls, mark: int) -> List[Tuple[Any, llm.Request]]:
    return list(zip(provider_calls.calls[mark:], provider_calls.fake.requests[mark:], strict=True))


def run_stack_p(
    monkeypatch,
    tmp_path: Path,
    provider_calls,
    *,
    max_tokens: int = 4000,
    context7_id: Optional[str] = LIBRARY_ID,
    questions: Optional[List[Dict[str, Any]]] = None,
) -> Observed:
    """Stack P end to end on the fixture: answer, then judge, through the real pipeline code."""
    from dokimi.orchestrator import pipeline as pipeline_mod

    client = RecordingDocClient()
    reranker = StubReranker()
    install_common(monkeypatch, client, reranker)
    answer_with_canned_reply(provider_calls)
    judge_inputs = install_judge_spy(monkeypatch)

    pipe = pipeline_mod.EvaluationPipeline(
        product=PRODUCT,
        output_dir=tmp_path,
        description=DESCRIPTION,
        model=ANSWER_MODEL,
        provider=ANSWER_PROVIDER,
        judge_model=JUDGE_MODEL,
        judge_provider=JUDGE_PROVIDER,
        top_k=TOP_K,
        max_tokens_per_question=max_tokens,
        context7_id=context7_id,
        product_key=PRODUCT_KEY,
    )
    qs = QUESTIONS if questions is None else questions
    mark = len(provider_calls.calls)
    answers = pipe._generate_answers(qs, concurrency=1)
    artifact = json.loads(pipe.answers_path.read_text(encoding="utf-8"))
    evaluations = pipe._evaluate_answers(answers, concurrency=1)
    return Observed(
        stack="P",
        client=client,
        reranker=reranker,
        answers=answers,
        evaluations=evaluations,
        judge_inputs=judge_inputs,
        artifact=artifact,
        requests=_requests_since(provider_calls, mark),
    )


def run_stack_a(
    monkeypatch,
    tmp_path: Path,
    provider_calls,
    *,
    max_tokens: Optional[int] = 4000,
    temperature: Optional[float] = 0.0,
    seed: Optional[int] = 1,
    top_k: int = TOP_K,
) -> Observed:
    """Stack A end to end on the fixture: answer under both arms, then judge."""
    from dokimi.eval.arm_runner import ArmRunner
    from dokimi.eval.judge import Judge
    from dokimi.treatments.arms import BaselineTreatment, DocTreatment

    client = RecordingDocClient()
    reranker = StubReranker()
    install_common(monkeypatch, client, reranker)
    answer_with_canned_reply(provider_calls)
    judge_inputs = install_judge_spy(monkeypatch)

    treatments = [
        BaselineTreatment("baseline"),
        DocTreatment(client, name="docs", top_k=top_k, reranker=reranker),
    ]
    runner = ArmRunner(
        treatments,
        model=ANSWER_MODEL,
        provider=ANSWER_PROVIDER,
        max_tokens=max_tokens,
        temperature=temperature,
        seed=seed,
    )
    mark = len(provider_calls.calls)
    records = runner.run(PRODUCT, QUESTIONS, library_id=LIBRARY_ID, concurrency=1)
    judge = Judge(model=JUDGE_MODEL, provider=JUDGE_PROVIDER)
    scored = runner.judge(judge, PRODUCT, records)
    artifact = runner.build_output(PRODUCT, records, evaluations=scored, judge=judge)
    return Observed(
        stack="A",
        client=client,
        reranker=reranker,
        answers=records,
        evaluations=scored,
        judge_inputs=judge_inputs,
        artifact=artifact,
        requests=_requests_since(provider_calls, mark),
    )


def answer_calls(obs: Observed, question_text: str) -> List[Tuple[str, llm.Request]]:
    """``(kind, request)`` for each answer call made for *question_text*, in send order.

    ``kind`` is ``"docs"`` when the prompt carries a documentation/context block,
    ``"plain"`` otherwise. Judge calls are left out.
    """
    out: List[Tuple[str, llm.Request]] = []
    for call, request in obs.requests:
        if call.purpose == JUDGE_PURPOSE:
            continue
        text = llm.prompt_text(request)
        if question_text not in text:
            continue
        kind = "docs" if (P_DOCS_MARKER in text or A_DOCS_MARKER in text) else "plain"
        out.append((kind, request))
    return out


def judge_inputs_for(obs: Observed, question_text: str) -> List[Dict[str, Any]]:
    """The judge inputs (both arms) recorded for one question."""
    return [j for j in obs.judge_inputs if j.get("question") == question_text]


def answer_record(obs: Observed, qid: str) -> Dict[str, Any]:
    return next(a for a in obs.answers if a["question_id"] == qid)


def docs_block(obs: Observed, rec: Dict[str, Any]) -> Dict[str, Any]:
    """The docs-arm block of one answer record, in either stack's shape."""
    return rec["with_docs"] if obs.stack == "P" else rec["arms"]["docs"]


def classify_answer(text: Optional[str]) -> str:
    """The fallback placeholder, the no-docs marker, a model reply, or nothing."""
    if not text:
        return "none"
    if text.startswith("[FALLBACK"):
        return "placeholder"
    if text.startswith("[No documentation"):
        return "no_docs_marker"
    return "model_reply"


def judge_context_strings(obs: Observed, question_text: str) -> List[str]:
    """The distinct judge contexts recorded for one question, sorted."""
    return sorted({j["context"] for j in judge_inputs_for(obs, question_text)})


def request_shape(request: llm.Request) -> Dict[str, Any]:
    """The sampling and message fields one answer request carried."""
    system = next((m["content"] for m in request.messages if m.get("role") == "system"), None)
    last = request.messages[-1].get("content") if request.messages else None
    return {
        "system": system,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "seed": request.seed,
        "cache_blocks": isinstance(last, list),
        "roles": tuple(m.get("role") for m in request.messages),
    }


def prompt_shape(text: str) -> Dict[str, Any]:
    """Where the labelled parts sit in a prompt, and which guidance phrases it carries."""
    labels = {
        "question": "**Question:**",
        "context": A_DOCS_MARKER,
        "documentation": P_DOCS_MARKER,
    }
    present = sorted((text.find(marker), name) for name, marker in labels.items() if marker in text)
    guidance = {
        "only_docs": "ONLY the provided documentation",
        "where_relevant": "where relevant",
    }
    return {
        "first_line": text.lstrip().splitlines()[0].strip(),
        "order": tuple(name for _, name in present),
        "guidance": tuple(name for name, marker in guidance.items() if marker in text),
    }


def context_shape(text: str, question_text: str) -> Dict[str, Any]:
    """Shape of one judge context: opening words, chunk count, and whether the first passage arrives whole.

    Stack P's judge sees the 200-character report snippet (see panel.py), so its passage is cut.
    """
    chunks = len(re.findall(r"^Doc \d+: ", text, re.M)) or (text.count("\n\n---\n\n") + 1)
    return {
        "starts_with": text[:7],
        "chunks": chunks,
        "whole_passage": _doc_text(question_text, 0) in text,
    }


def distinct(values) -> Any:
    """The single value when all are equal, else the distinct values in first-seen order."""
    out: List[Any] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out[0] if len(out) == 1 else out
