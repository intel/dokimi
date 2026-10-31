# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Provider-call budgets: how many requests each key path sends, pinned exactly.

A cache that stops hitting, a retry loop that runs once more, a judge called per
sample instead of per record: each multiplies spend without changing a score, so
nothing else in the suite notices. These tests count the requests that reach the
``dokimi.llm`` adapter seam (``provider_calls`` fixture, ``tests/provider_calls.py``)
and fail on any change, up or down. A deliberate change to a path's call pattern
updates its number here, in the same PR.

Budgets, per record or question (both arms answered):

- single judge: 2 calls (one per arm); an unparseable reply is re-drawn, at most 3
  draws per arm;
- panel of k judges: 2k calls;
- answer generation: 2 calls (context arm, baseline);
- question validation of n questions: n calls plus 1 batched embeddings call;
- a transient provider error: retried ``max_retries`` (3) times, so at most 4
  requests per call;
- a rerun with the response cache on: 0 calls.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from dokimi import caching
from dokimi.cli import build_parser
from dokimi.eval.judge import Judge
from dokimi.eval.panel import DEFAULT_PANEL, JudgeConfig, JudgePanel

ANSWERS = [
    {
        "question_id": "Q1",
        "question_text": "How do I split a loop?",
        "with_docs": {"answer": "with docs", "retrieved_docs": [{"snippet": "parallel_for"}]},
        "without_docs": {"answer": "without docs"},
    }
]

#: A transient error dokimi.llm.is_retryable retries.
RATE_LIMITED = RuntimeError("rate limit exceeded")
UNPARSEABLE = "not json"


def _judge(provider: str = "openai", model: str = "gpt-x") -> Judge:
    return Judge(model=model, provider=provider)


def _panel(k: int) -> JudgePanel:
    return JudgePanel(
        judges=[
            JudgeConfig(role=role, model="gpt-x", provider="openai") for role in DEFAULT_PANEL[:k]
        ],
        concurrency=1,
    )


def _run(argv):
    args = build_parser().parse_args(argv)
    args.func(args)


# ── single judge ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider, model, labels",
    [
        # The shim path records the model it was given; the get_llm path the litellm route.
        ("openai", "gpt-x", ("openai", "gpt-x")),
        ("amazon-bedrock", "us.anthropic.x", ("litellm", "bedrock/us.anthropic.x")),
    ],
)
def test_judge_one_record_makes_2_provider_calls(provider_calls, provider, model, labels):
    (row,) = _judge(provider, model).evaluate_answers("lib", ANSWERS)
    assert [(c.provider, c.model, c.purpose) for c in provider_calls.calls] == [
        (*labels, "judge"),
        (*labels, "judge"),
    ]
    assert row["delta"] == 0.0 and row["with_docs"]["aggregate"] == 80.0


def test_judge_record_with_only_a_baseline_answer_makes_1_provider_call(provider_calls):
    record = {**ANSWERS[0], "with_docs": None}
    (row,) = _judge().evaluate_answers("lib", [record])
    assert provider_calls.by_purpose() == Counter(judge=1)
    assert row["with_docs"] is None and row["without_docs"]["aggregate"] == 80.0


def test_judge_unparseable_replies_make_3_provider_calls_per_arm(provider_calls):
    # The context arm never parses: 3 draws, then its error; the baseline arm is not judged.
    provider_calls.push(UNPARSEABLE, UNPARSEABLE, UNPARSEABLE)
    (row,) = _judge().evaluate_answers("lib", ANSWERS)
    assert provider_calls.by_purpose() == Counter(judge=3)
    assert "error" in row and row["delta"] is None


def test_judge_one_redraw_makes_3_provider_calls_per_record(provider_calls):
    provider_calls.push(UNPARSEABLE)
    (row,) = _judge().evaluate_answers("lib", ANSWERS)
    assert provider_calls.by_purpose() == Counter(judge=3)
    assert row["delta"] == 0.0


# ── panel ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("k", [1, 2, 3])
def test_panel_of_k_judges_one_record_makes_2k_provider_calls(provider_calls, k):
    (row,) = _panel(k).evaluate_answers(ANSWERS, "lib")
    roles = DEFAULT_PANEL[:k]
    assert provider_calls.by_purpose() == Counter({f"panel:{r}": 2 for r in roles})
    assert len(provider_calls.calls) == 2 * k
    assert row["panel_size"] == k and row["delta"] == 0.0


def test_panel_judge_with_unparseable_replies_makes_3_provider_calls(provider_calls):
    provider_calls.push(UNPARSEABLE, UNPARSEABLE, UNPARSEABLE)
    verdict = _panel(1).evaluate("q", "a", "lib")
    assert provider_calls.by_purpose() == Counter({"panel:technical_expert": 3})
    assert verdict.mean_aggregate is None and verdict.votes[0].error


def test_panel_one_redraw_makes_2_provider_calls_per_judge(provider_calls):
    provider_calls.push(UNPARSEABLE)
    verdict = _panel(1).evaluate("q", "a", "lib")
    assert provider_calls.by_purpose() == Counter({"panel:technical_expert": 2})
    assert verdict.mean_aggregate is not None


# ── response cache ───────────────────────────────────────────────────────────


@pytest.fixture
def cache_on(tmp_path, monkeypatch):
    monkeypatch.setenv(caching.RESPONSE_CACHE_ENV, str(tmp_path / "responses"))


@pytest.mark.parametrize(
    "provider, model", [("openai", "gpt-x"), ("amazon-bedrock", "us.anthropic.x")]
)
def test_judge_rerun_on_a_cache_hit_makes_0_provider_calls(
    provider_calls, cache_on, provider, model
):
    first = _judge(provider, model).evaluate_answers("lib", ANSWERS)
    assert len(provider_calls.calls) == 2
    provider_calls.reset()
    second = _judge(provider, model).evaluate_answers("lib", ANSWERS)
    assert provider_calls.calls == []
    assert second[0]["with_docs"] == first[0]["with_docs"]


def test_judge_rerun_after_an_unparseable_reply_makes_0_provider_calls(provider_calls, cache_on):
    # The first run stores every attempt it asked; a rerun replays the stored retry
    # instead of asking the unparseable attempt live again (#180). The draw and
    # cache_if keys are pinned in test_judge_response_cache, not here.
    provider_calls.push(UNPARSEABLE)
    (first,) = _judge().evaluate_answers("lib", ANSWERS)
    assert len(provider_calls.calls) == 3
    for _ in range(2):  # every rerun, not only the first
        provider_calls.reset()
        (row,) = _judge().evaluate_answers("lib", ANSWERS)
        assert provider_calls.calls == []
        assert row["with_docs"] == first["with_docs"]


def test_panel_rerun_after_an_unparseable_reply_makes_0_provider_calls(provider_calls, cache_on):
    # Same replay as the single judge. The panel's draw and cache_if keys are pinned
    # in test_panel_properties and test_panel_reply_validation, not here.
    provider_calls.push(UNPARSEABLE)
    first = _panel(1).evaluate("q", "a", "lib")
    assert len(provider_calls.calls) == 2
    for _ in range(2):  # every rerun, not only the first
        provider_calls.reset()
        verdict = _panel(1).evaluate("q", "a", "lib")
        assert provider_calls.calls == []
        assert verdict.mean_aggregate == first.mean_aggregate
        assert verdict.mean_aggregate is not None


def test_panel_rerun_on_a_cache_hit_makes_0_provider_calls(provider_calls, cache_on):
    _panel(3).evaluate_answers(ANSWERS, "lib")
    assert len(provider_calls.calls) == 6
    provider_calls.reset()
    (row,) = _panel(3).evaluate_answers(ANSWERS, "lib")
    assert provider_calls.calls == [] and row["delta"] == 0.0


# ── transport retry ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider, model", [("openai", "gpt-x"), ("amazon-bedrock", "us.anthropic.x")]
)
def test_judge_transient_error_makes_4_provider_calls_then_fails_the_record(
    provider_calls, provider, model
):
    # max_retries=3 on both transports (the llm_call default and the get_llm one):
    # 1 attempt + 3 retries, then the record fails. The baseline arm is not judged
    # once the context arm raised.
    provider_calls.push(*[RATE_LIMITED] * 4)
    (row,) = _judge(provider, model).evaluate_answers("lib", ANSWERS)
    assert provider_calls.by_purpose() == Counter(judge=4)
    assert provider_calls.sleeps == [2.0, 4.0, 8.0]
    assert "rate limit" in row["error"]


def test_judge_non_retryable_error_makes_1_provider_call_and_no_backoff(provider_calls):
    # A bad request or key will not succeed on a retry: one request, no sleeps.
    provider_calls.push(ValueError("bad request"))
    (row,) = _judge().evaluate_answers("lib", ANSWERS)
    assert provider_calls.by_purpose() == Counter(judge=1)
    assert provider_calls.sleeps == []
    assert "bad request" in row["error"]


def test_judge_one_transient_error_makes_3_provider_calls_per_record(provider_calls):
    provider_calls.push(RATE_LIMITED)
    (row,) = _judge().evaluate_answers("lib", ANSWERS)
    assert provider_calls.by_purpose() == Counter(judge=3)
    assert provider_calls.sleeps == [2.0] and row["delta"] == 0.0


def test_panel_judge_transient_error_makes_4_provider_calls(provider_calls):
    # The panel's own redraw loop does not retry a transport fault: it propagates
    # after the transport's 4 requests and the vote records it.
    provider_calls.push(*[RATE_LIMITED] * 4)
    verdict = _panel(1).evaluate("q", "a", "lib")
    assert provider_calls.by_purpose() == Counter({"panel:technical_expert": 4})
    assert "rate limit" in verdict.votes[0].error


def test_answer_transient_error_makes_4_provider_calls(provider_calls):
    from dokimi.eval.answerer import Answerer

    provider_calls.push(*[RATE_LIMITED] * 4)
    with pytest.raises(RuntimeError, match="rate limit"):
        Answerer(model="gpt-x", provider="openai")._generate_without_docs("How?")
    assert provider_calls.by_purpose() == Counter({"answer:without_docs": 4})


# ── question validation (the baselined embeddings client) ───────────────────


def test_validating_3_questions_makes_4_provider_calls(provider_calls, monkeypatch):
    from dokimi.questions.validator import QuestionValidator

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    questions = [
        {"question": q}
        for q in (
            "How do I use parallel_for?",
            "How do I size a task_arena?",
            "What does a flow graph node own?",
        )
    ]
    validator = QuestionValidator(
        llm_model="gpt-x", llm_provider="openai", embedding_provider="openai"
    )
    kept, stats = validator.validate_and_dedupe("lib", questions)
    assert provider_calls.by_purpose() == Counter(validate=3, embeddings=1)
    assert len(kept) == 3 and stats["deduplication_performed"] is True


# ── CLI ──────────────────────────────────────────────────────────────────────


def _answers_file(tmp_path, n: int) -> str:
    path = tmp_path / "answers.json"
    path.write_text(
        json.dumps([{**ANSWERS[0], "question_id": f"Q{i}"} for i in range(n)]), encoding="utf-8"
    )
    return str(path)


def test_eval_score_of_2_records_makes_4_provider_calls(provider_calls, tmp_path):
    out = tmp_path / "eval.json"
    _run(
        [
            "eval", "score", "pointwise",
            "--product",
            "lib",
            "--answers",
            _answers_file(tmp_path, 2),
            "--output",
            str(out),
            "--judge-provider",
            "openai",
            "--judge-model",
            "gpt-x",
        ]
    )
    assert provider_calls.by_purpose() == Counter(judge=4)
    assert [e["delta"] for e in json.loads(out.read_text())["evaluations"]] == [0.0, 0.0]


def test_eval_panel_score_of_1_record_with_3_judges_makes_6_provider_calls(
    provider_calls, tmp_path
):
    out = tmp_path / "panel.json"
    _run(
        [
            "eval", "score", "panel",
            "--product",
            "lib",
            "--answers",
            _answers_file(tmp_path, 1),
            "--output",
            str(out),
            "--provider",
            "openai",
            "--model",
            "gpt-x",
        ]
    )
    assert provider_calls.by_purpose() == Counter({f"panel:{r}": 2 for r in DEFAULT_PANEL})
    assert json.loads(out.read_text())["evaluations"][0]["panel_size"] == 3


class _DocSource:
    """A doc-source client with one relevant chunk per query."""

    def get_library_docs(self, library_id, query, max_tokens):
        return [{"content": f"{query} Use parallel_for with a blocked_range.", "source": "s"}]


def _answers_generate(tmp_path, monkeypatch, out):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "dokimi.mcp.factory.create_doc_source_client", lambda *a, **kw: _DocSource()
    )
    monkeypatch.setattr("dokimi.eval.answerer.SENTENCE_TRANSFORMERS_AVAILABLE", False)
    questions = tmp_path / "questions.json"
    questions.write_text(
        json.dumps(
            [
                {"id": "q1", "question": "How do I split a loop?"},
                {"id": "q2", "question": "How do I size a task_arena?"},
            ]
        ),
        encoding="utf-8",
    )
    _run(
        [
            "answers",
            "generate",
            "--product",
            "lib",
            "--questions",
            str(questions),
            "--context7-id",
            "org/lib",
            "--output",
            str(out),
            "--provider",
            "openai",
            "--model",
            "gpt-x",
            "--rerank-threshold",
            "0",
            "--concurrency",
            "1",
        ]
    )


def test_answers_generate_of_2_questions_makes_4_provider_calls(
    provider_calls, tmp_path, monkeypatch
):
    out = tmp_path / "answers.json"
    _answers_generate(tmp_path, monkeypatch, out)
    assert provider_calls.by_purpose() == Counter({"answer:with_docs": 2, "answer:without_docs": 2})
    answers = json.loads(out.read_text())["answers"]
    assert [a["with_docs"]["answer"] for a in answers] == [
        "Call parallel_for over a blocked_range."
    ] * 2


def test_answers_generate_rerun_on_a_cache_hit_makes_0_provider_calls(
    provider_calls, tmp_path, monkeypatch, cache_on
):
    out = tmp_path / "answers.json"
    _answers_generate(tmp_path, monkeypatch, out)
    assert len(provider_calls.calls) == 4
    provider_calls.reset()
    _answers_generate(tmp_path, monkeypatch, out)
    assert provider_calls.calls == []
