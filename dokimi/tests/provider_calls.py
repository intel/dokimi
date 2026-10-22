# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A ledger of provider calls, for tests that pin how many calls a path makes.

Every chat completion leaves the framework through one seam,
:data:`dokimi.llm.Adapter` (``LLM._send`` hands each attempt to it, retries
included, and a response-cache hit never reaches it). :class:`ProviderCalls`
sits at that seam: it records one :class:`ProviderCall` per request and answers
from a :class:`dokimi.llm.FakeAdapter`, scripted first and then from a canned
reply per *purpose*. The one baselined SDK use with its own requests that a
budget covers, the validator's OpenAI embeddings client, is replaced by
:class:`FakeOpenAI`, which records into the same ledger.

The ``provider_calls`` fixture (``tests/conftest.py``) installs both, turns the
response cache off and makes retry backoff instant. A count read from
``provider_calls.calls`` is what the code under test sent, not a mock's
``call_count``.
"""

from __future__ import annotations

import json
import threading
from collections import Counter
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, List, Optional

from dokimi import llm

#: A judge reply that parses on the single-judge and the panel path alike.
JUDGE_REPLY = json.dumps(
    {
        "reasoning": "r",
        "correctness": 80,
        "completeness": 80,
        "specificity": 80,
        "code_quality": 80,
        "actionability": 80,
        "factual_grounding": 80,
        "aggregate": 0,
    }
)
ANSWER_REPLY = "Call parallel_for over a blocked_range."
VALIDATION_REPLY = json.dumps(
    {"relevance": 90, "answerability": 90, "specificity": 90, "aggregate": 90}
)


def _first_line(template: str) -> str:
    return template.lstrip().splitlines()[0].strip()


def _purposes() -> List[tuple]:
    """``(first prompt line, purpose)`` for every prompt a budgeted path sends.

    Read from the templates themselves, so a reworded prompt keeps its purpose.
    """
    from dokimi.eval.answerer import ANSWER_PROMPT_WITH_DOCS, ANSWER_PROMPT_WITHOUT_DOCS
    from dokimi.eval.judge import JUDGE_PROMPT, JUDGE_PROMPT_GROUNDED
    from dokimi.eval.panel import _ROLE_INTROS
    from dokimi.questions.validator import VALIDATION_PROMPT

    table = [(_first_line(intro), f"panel:{role}") for role, intro in _ROLE_INTROS.items()]
    table += [
        (_first_line(JUDGE_PROMPT), "judge"),
        (_first_line(JUDGE_PROMPT_GROUNDED), "judge"),
        (_first_line(ANSWER_PROMPT_WITH_DOCS), "answer:with_docs"),
        (_first_line(ANSWER_PROMPT_WITHOUT_DOCS), "answer:without_docs"),
        (_first_line(VALIDATION_PROMPT), "validate"),
    ]
    return table


def purpose_of(prompt: str) -> str:
    """Which budgeted prompt *prompt* is; ``"other"`` when none."""
    head = prompt.lstrip().splitlines()[0].strip() if prompt.strip() else ""
    for line, purpose in _purposes():
        if head == line:
            return purpose
    return "other"


_CANNED = {
    "judge": JUDGE_REPLY,
    "validate": VALIDATION_REPLY,
    "answer:with_docs": ANSWER_REPLY,
    "answer:without_docs": ANSWER_REPLY,
}


def canned_reply(purpose: str) -> str:
    """The default reply for a request of *purpose*: one that parses."""
    if purpose.startswith("panel:"):
        return JUDGE_REPLY
    if purpose not in _CANNED:
        raise AssertionError(f"provider_calls: no canned reply for a {purpose!r} prompt")
    return _CANNED[purpose]


@dataclass(frozen=True)
class ProviderCall:
    """One request that reached the provider seam (each retry is its own)."""

    provider: str
    model: str
    purpose: str


class ProviderCalls:
    """An :data:`dokimi.llm.Adapter` that records every request, then answers it.

    ``push(*items)`` scripts the next replies (text, a ``Reply`` or an exception to
    raise), as :class:`dokimi.llm.FakeAdapter` takes them; an unscripted request
    gets :func:`canned_reply` for its purpose.
    """

    def __init__(self) -> None:
        self.fake = llm.FakeAdapter(respond=lambda r: canned_reply(purpose_of(llm.prompt_text(r))))
        self.calls: List[ProviderCall] = []
        self.sleeps: List[float] = []
        self._lock = threading.Lock()

    def __call__(self, request: llm.Request) -> llm.Reply:
        self.record(
            ProviderCall(request.provider, request.model, purpose_of(llm.prompt_text(request)))
        )
        return self.fake(request)

    def record(self, call: ProviderCall) -> None:
        with self._lock:
            self.calls.append(call)

    def push(self, *items: Any) -> "ProviderCalls":
        self.fake.push(*items)
        return self

    def by_purpose(self) -> Counter:
        """How many calls each purpose made."""
        return Counter(c.purpose for c in self.calls)

    def reset(self) -> None:
        """Forget the calls so far (between two runs of the same path)."""
        with self._lock:
            self.calls.clear()


class FakeOpenAI:
    """Stands in for ``openai.OpenAI`` where the validator embeds questions.

    Each ``embeddings.create`` is one provider call, recorded with purpose
    ``"embeddings"``. Every text gets its own unit vector, so no two are duplicates.
    """

    def __init__(self, ledger: ProviderCalls, api_key: Optional[str] = None, **_: Any):
        self.embeddings = SimpleNamespace(create=self._create)
        self._ledger = ledger

    def _create(self, *, input: List[str], model: str) -> Any:
        self._ledger.record(ProviderCall("openai", model, "embeddings"))
        slots = {text: i for i, text in enumerate(dict.fromkeys(input))}
        data = []
        for text in input:
            vec = [0.0] * len(slots)
            vec[slots[text]] = 1.0
            data.append(SimpleNamespace(embedding=vec))
        return SimpleNamespace(data=data)
