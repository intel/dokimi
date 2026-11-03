# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The benchmark preflight and the RAGAS judge read a key as ``dokimi.llm`` does.

A key may be ``file:<path>`` (``llm.resolve_api_key``). The preflight passed an
unreadable or empty key file that every call then failed on, and the RAGAS judge
sent the ``file:`` string itself as the key.
"""

from __future__ import annotations

import sys
import types

import pytest

from dokimi import cli
from dokimi.commands.benchmark import _credential_error
from dokimi.defaults import DEFAULT_JUDGE_PROVIDER
from dokimi.eval.judge import JUDGE_PROVIDERS
from dokimi.eval.ragas_eval import RagasEvaluator
from dokimi.llm import ANSWER_PROVIDERS, api_key_env
from dokimi.llm_cli import BACKENDS

API_PROVIDERS = sorted({*ANSWER_PROVIDERS, *JUDGE_PROVIDERS} - set(BACKENDS))


def _preflight(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["dokimi", "benchmark", "preflight", "--library", "onetbb", "--provider", "anthropic",
         "--judge-provider", "anthropic", "--output-dir", str(tmp_path / "out")],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code, capsys.readouterr().err


def test_preflight_refuses_an_unreadable_key_file(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "no-such-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", f"file:{missing}")
    code, err = _preflight(tmp_path, monkeypatch, capsys)
    assert f"Cannot read API key from file '{missing}'" in err
    assert "(ANTHROPIC_API_KEY, provider anthropic)" in err
    assert code == 2


def test_preflight_refuses_an_empty_key_file(tmp_path, monkeypatch, capsys):
    empty = tmp_path / "key"
    empty.write_text("\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", f"file:{empty}")
    code, err = _preflight(tmp_path, monkeypatch, capsys)
    assert "ANTHROPIC_API_KEY for provider anthropic names an empty key file" in err
    assert code == 2


@pytest.mark.parametrize("provider", API_PROVIDERS)
def test_preflight_names_the_variable_dokimi_llm_reads(provider, tmp_path, monkeypatch):
    for var in {api_key_env(p) for p in API_PROVIDERS}:
        monkeypatch.delenv(var, raising=False)
    var = api_key_env(provider)
    assert _credential_error(provider) == f"missing {var} for provider {provider}"
    key = tmp_path / "key"
    key.write_text("sk-test\n")
    monkeypatch.setenv(var, f"file:{key}")
    assert _credential_error(provider) is None


@pytest.fixture
def ragas_stubs(monkeypatch):
    """Stub ragas and the LangChain classes, recording each chat model and embeddings built."""
    built = []

    def record(name):
        def build(**kwargs):
            built.append((name, kwargs))
            return name, kwargs
        return build

    stubs = {
        "ragas.llms": {"LangchainLLMWrapper": lambda model: model},
        "ragas.embeddings": {"LangchainEmbeddingsWrapper": lambda embeddings: embeddings},
        "ragas.metrics": {name: types.SimpleNamespace(embeddings="unset")
                          for name in ("faithfulness", "answer_relevancy", "context_precision")},
        "langchain_openai": {"ChatOpenAI": record("openai"), "OpenAIEmbeddings": record("embeddings")},
        "langchain_anthropic": {"ChatAnthropic": record("anthropic")},
        "langchain_core.language_models": {"BaseChatModel": object},
    }
    for name, attrs in stubs.items():
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))
    return built


@pytest.fixture
def chat_models(ragas_stubs, monkeypatch):
    """The chat models built, with no metric and no embeddings built."""
    monkeypatch.setattr(RagasEvaluator, "_build_metrics", lambda self: [])
    monkeypatch.setattr(RagasEvaluator, "_build_ragas_embeddings", lambda self: None)
    return ragas_stubs


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_ragas_judge_reads_a_key_file_from_the_environment(provider, chat_models, tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("sk-from-file\n")
    monkeypatch.setenv(api_key_env(provider), f"file:{key}")
    RagasEvaluator(llm_model="m", provider=provider)
    assert chat_models == [(provider, {"model": "m", "api_key": "sk-from-file"})]


def test_ragas_judge_reads_an_explicit_key_file(chat_models, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    key = tmp_path / "key"
    key.write_text("sk-explicit\n")
    RagasEvaluator(llm_model="m", provider="openai", api_key=f"file:{key}")
    assert chat_models == [("openai", {"model": "m", "api_key": "sk-explicit"})]


def test_ragas_fallback_judge_reads_its_own_key_file(chat_models, tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("sk-judge\n")
    monkeypatch.setenv(api_key_env(DEFAULT_JUDGE_PROVIDER), f"file:{key}")
    RagasEvaluator(llm_model="gemini-x", provider="google", api_key="sk-for-google")
    ((name, kwargs),) = chat_models
    assert (name, kwargs["api_key"]) == (DEFAULT_JUDGE_PROVIDER, "sk-judge")


def test_ragas_judge_raises_on_an_unreadable_key_file(chat_models, tmp_path, monkeypatch):
    missing = tmp_path / "no-such-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", f"file:{missing}")
    with pytest.raises(ValueError, match="Cannot read API key from file"):
        RagasEvaluator(provider="anthropic")
    assert chat_models == []


def test_ragas_judge_without_a_key_leaves_it_to_langchain(chat_models, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    RagasEvaluator(llm_model="m", provider="openai")
    assert chat_models == [("openai", {"model": "m", "api_key": None})]


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_ragas_judge_raises_on_an_empty_key_file(provider, chat_models, tmp_path, monkeypatch):
    # Given no key, langchain would read the variable itself and send the file: string.
    empty = tmp_path / "key"
    empty.write_text("  \n")
    monkeypatch.setenv(api_key_env(provider), f"file:{empty}")
    with pytest.raises(ValueError, match=f"{api_key_env(provider)} for provider {provider} names an empty key file"):
        RagasEvaluator(provider=provider)
    with pytest.raises(ValueError, match=f"api_key for provider {provider} names an empty key file"):
        RagasEvaluator(provider=provider, api_key=f"file:{empty}")
    assert chat_models == []


def test_ragas_fallback_judge_raises_on_an_empty_key_file(chat_models, tmp_path, monkeypatch):
    empty = tmp_path / "key"
    empty.write_text("")
    monkeypatch.setenv(api_key_env(DEFAULT_JUDGE_PROVIDER), f"file:{empty}")
    with pytest.raises(ValueError, match="names an empty key file"):
        RagasEvaluator(provider="google")
    assert chat_models == []


def _answer_relevancy():
    return sys.modules["ragas.metrics"].answer_relevancy


def _embeddings(key):
    return "embeddings", {"model": "text-embedding-ada-002", "openai_api_key": key}


def test_ragas_embeddings_read_a_key_file(ragas_stubs, tmp_path, monkeypatch):
    # ragas embeds answer_relevancy with OpenAI whatever the judge; its default read file: as is.
    key = tmp_path / "key"
    key.write_text("sk-embed\n")
    monkeypatch.setenv("OPENAI_API_KEY", f"file:{key}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-judge")
    evaluator = RagasEvaluator(provider="anthropic", metrics=["answer_relevancy"])
    assert evaluator._ragas_embeddings == _embeddings("sk-embed")
    assert _answer_relevancy().embeddings == "unset"


def test_each_evaluator_keeps_its_own_embeddings(ragas_stubs, monkeypatch):
    # ragas's answer_relevancy is one module-level object that every evaluator shares.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-A")
    first = RagasEvaluator(provider="openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-B")
    second = RagasEvaluator(provider="openai")
    monkeypatch.delenv("OPENAI_API_KEY")
    RagasEvaluator(provider="anthropic", api_key="sk-judge")
    assert first._ragas_embeddings == _embeddings("sk-A")
    assert second._ragas_embeddings == _embeddings("sk-B")
    assert _answer_relevancy().embeddings == "unset"


def test_both_arms_are_scored_with_the_evaluators_embeddings(ragas_stubs, monkeypatch):
    calls = []

    def ragas_evaluate(dataset, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop after the call")  # evaluate logs it and goes on

    monkeypatch.setenv("OPENAI_API_KEY", "sk-A")
    evaluator = RagasEvaluator(provider="openai")
    monkeypatch.setattr("dokimi.eval.ragas_eval._import_ragas_runtime",
                        lambda: (types.SimpleNamespace(from_list=list), ragas_evaluate))
    answers = [{"question_id": "q1", "question_text": "How?",
                "with_docs": {"answer": "a", "retrieved_docs": [{"snippet": "s"}]},
                "without_docs": {"answer": "b"}}]
    evaluator.evaluate(answers, include_without_docs=True)
    assert [call["embeddings"] for call in calls] == [_embeddings("sk-A")] * 2


def test_ragas_embeddings_without_a_key_stay_ragas_default(ragas_stubs, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    evaluator = RagasEvaluator(provider="openai", api_key="sk-judge", metrics=["answer_relevancy"])
    assert evaluator._ragas_embeddings is None
    assert [name for name, _ in ragas_stubs] == ["openai"]


@pytest.mark.parametrize("content", [None, ""])
def test_ragas_embeddings_raise_on_a_bad_key_file(content, ragas_stubs, tmp_path, monkeypatch):
    key = tmp_path / "key"
    if content is not None:
        key.write_text(content)
    monkeypatch.setenv("OPENAI_API_KEY", f"file:{key}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-judge")
    with pytest.raises(ValueError, match="Cannot read API key from file|names an empty key file"):
        RagasEvaluator(provider="anthropic", metrics=["answer_relevancy"])


def test_ragas_builds_no_embeddings_without_answer_relevancy(ragas_stubs, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", f"file:{tmp_path / 'no-such-key'}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-judge")
    evaluator = RagasEvaluator(provider="anthropic", metrics=["faithfulness"])
    assert evaluator._ragas_embeddings is None
    assert [name for name, _ in ragas_stubs] == ["anthropic"]


def test_a_key_file_that_is_not_utf8_is_named_in_the_refusal(tmp_path, monkeypatch, capsys):
    key = tmp_path / "key"
    key.write_bytes(b"\xff\xfe")
    monkeypatch.setenv("ANTHROPIC_API_KEY", f"file:{key}")
    code, err = _preflight(tmp_path, monkeypatch, capsys)
    assert f"Cannot read API key from file '{key}'" in err
    assert code == 2
