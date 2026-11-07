# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The judge, persona generator, question generator and validator route through :mod:`dokimi.llm`.

They reached litellm through a provider table of their own (before the move to ``dokimi.llm``), which differed from the one
the answerer uses (#229 P1-2). Each test builds the real caller and reads the request it sends at
the :mod:`dokimi.llm` adapter seam (``fake_llm``).
"""

import pytest

from dokimi import caching
from dokimi.cli import parse_args
from dokimi.eval import arms_run
from dokimi.eval.arms_run import ArmsRunConfig
from dokimi.eval.judge import Judge
from dokimi.llm import CALLER_PROVIDERS, PROVIDERS, check_caller_provider, credential_env_var, resolve_model
from dokimi.llm_cli import BACKENDS
from dokimi.personas.generator import PersonaGenerator
from dokimi.questions.llm_gen import QuestionGenerator
from dokimi.questions.validator import QuestionValidator
from dokimi.utils import InputError, check_bedrock_model

CALLERS = {
    "judge": lambda model, provider, api_key: Judge(model=model, provider=provider, api_key=api_key),
    "personas": lambda model, provider, api_key: PersonaGenerator(model, provider, api_key),
    "questions": lambda model, provider, api_key: QuestionGenerator(model, provider, api_key),
    "validator": lambda model, provider, api_key: QuestionValidator(
        llm_model=model, llm_provider=provider, api_key=api_key),
}

ARN = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc123"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in {*PROVIDERS.values(), "ANTHROPIC_DEFAULT_SONNET_MODEL", caching.RESPONSE_CACHE_ENV}:
        monkeypatch.delenv(var, raising=False)


def sent(caller, fake_llm, model, provider, api_key=None):
    """The one request *caller*'s model sends for a prompt."""
    fake_llm.push("{}")
    CALLERS[caller](model, provider, api_key).llm.invoke("q")
    (request,) = fake_llm.requests
    return request


@pytest.mark.parametrize("caller", CALLERS)
def test_an_unknown_bare_bedrock_name_is_sent_as_given(caller, fake_llm, monkeypatch):
    # The old path sent the box's default Sonnet instead. A run that pays for answers first refuses it (below).
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.claude-sonnet-4-5")
    request = sent(caller, fake_llm, "claude-sonnet-4", "amazon-bedrock")
    assert request.litellm_model == "bedrock/claude-sonnet-4"


@pytest.mark.parametrize("caller", CALLERS)
def test_an_explicit_bedrock_key_is_sent(caller, fake_llm, monkeypatch):
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "from-env")
    assert sent(caller, fake_llm, "us.anthropic.x", "amazon-bedrock", "explicit").api_key == "explicit"


@pytest.mark.parametrize("caller", CALLERS)
def test_an_unreadable_key_file_raises_before_a_request(caller, fake_llm, tmp_path):
    # The old path sent the literal "file:..." text as the key.
    model = CALLERS[caller]("x/y", "openrouter", f"file:{tmp_path / 'missing'}").llm
    with pytest.raises(ValueError, match="Cannot read API key"):
        model.invoke("q")
    assert fake_llm.requests == []


@pytest.mark.parametrize("caller", CALLERS)
def test_vertex_ai_reads_the_gemini_key(caller, fake_llm, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    request = sent(caller, fake_llm, "gemini-2.5-pro", "vertex_ai")
    assert (request.litellm_model, request.api_key) == ("vertex_ai/gemini-2.5-pro", "gemini-key")


@pytest.mark.parametrize("caller", CALLERS)
def test_openrouter_has_no_output_ceiling_of_its_own(caller, fake_llm, monkeypatch):
    # The old path capped OpenRouter at 2000 output tokens; the answerer there never was.
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    request = sent(caller, fake_llm, "x/y", "openrouter")
    assert (request.litellm_model, request.api_key, request.max_tokens) == ("openrouter/x/y", "or-key", None)


@pytest.mark.parametrize("caller", CALLERS)
def test_a_bedrock_arn_keeps_the_bedrock_route(caller, fake_llm):
    assert sent(caller, fake_llm, ARN, "amazon-bedrock").litellm_model == f"bedrock/{ARN}"


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("model, route", [("gemini/gemini-2.5-pro", "bedrock/gemini/gemini-2.5-pro"),
    ("openai/gpt-4o", "bedrock/openai/gpt-4o"), ("bedrock/us.anthropic.x", "bedrock/us.anthropic.x")])
def test_a_bedrock_token_stays_on_the_bedrock_route(caller, model, route, fake_llm, monkeypatch):
    # Passed through, gemini/x is another vendor's route, and litellm sends it the Bedrock token.
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "tok")
    request = sent(caller, fake_llm, model, "amazon-bedrock")
    assert (request.litellm_model, request.api_key) == (route, "tok")


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("model", ["meta/llama-3.1-405b-instruct-maas", "mistralai/mistral-large"])
def test_a_vertex_model_garden_name_keeps_the_vertex_route(caller, model, fake_llm):
    # Its "/" is a publisher, not a litellm route: without vertex_ai/ litellm cannot place it.
    assert sent(caller, fake_llm, model, "google-vertex").litellm_model == f"vertex_ai/{model}"


def test_the_answerer_table_routes_a_bedrock_arn_and_vertex_ai():
    # An ARN has a "/", which the table passed through without a route; litellm cannot place it.
    assert resolve_model(ARN, "amazon-bedrock") == f"bedrock/{ARN}"
    assert credential_env_var("vertex_ai") == "GEMINI_API_KEY"
    assert resolve_model("meta/llama-3.1-405b-instruct-maas", "google") == "vertex_ai/meta/llama-3.1-405b-instruct-maas"
    # A litellm route of its own passes through: Google AI Studio, or an explicit Vertex one.
    assert [resolve_model(m, "google-vertex") for m in ("gemini/gemini-2.5-pro", "vertex_ai/x")] == [
        "gemini/gemini-2.5-pro", "vertex_ai/x"]


BARE = "claude-sonnet-4"  # off BEDROCK_MODEL_IDS: the old path ran the box's default Sonnet for it


@pytest.mark.parametrize("model", [BARE, "claude", "sonnet", "claude-sonnet-4.5"])
def test_a_bare_bedrock_name_off_the_table_is_refused(model):
    with pytest.raises(InputError, match=f"Bedrock has no model '{model}'"):
        check_bedrock_model("amazon-bedrock", model)


@pytest.mark.parametrize("provider, model", [
    ("amazon-bedrock", "claude-sonnet-5-5"), ("amazon-bedrock", "us.anthropic.claude-sonnet-4-6"),
    ("amazon-bedrock", "amazon.nova-pro-v1:0"), ("amazon-bedrock", "ai21.jamba-1-5-large-v1:0"),
    ("amazon-bedrock", ARN),
    ("amazon-bedrock", "bedrock/us.anthropic.x"), ("anthropic", BARE), ("openrouter", BARE),
])
def test_a_name_bedrock_can_run_or_another_provider_passes(provider, model):
    check_bedrock_model(provider, model)


@pytest.fixture
def sonnet_box(fake_llm, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.claude-sonnet-4-5")
    yield fake_llm
    assert fake_llm.requests == []  # refused before any call


# A run that pays for answers before its first judge call refuses the name first, with or without
# a temperature. The commands that judge stored answers fail on that call instead.
def test_arms_run_refuses_it_before_any_answer(sonnet_box):
    config = ArmsRunConfig(product="demo", judge_provider="amazon-bedrock", judge_model=BARE)
    with pytest.raises(InputError):
        arms_run._check_judge(config, [{"id": "q1", "question": "How?"}])


@pytest.mark.parametrize("argv", [
    ["evaluate", "--product", "onedal", "--description", "x"],
    ["benchmark", "run", "--library", "onedal"],
    ["benchmark", "batch"],
])
def test_the_pipeline_commands_refuse_it_before_any_answer(argv, sonnet_box, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = parse_args([*argv, "--judge-provider", "amazon-bedrock", "--judge-model", BARE])
    with pytest.raises(InputError, match=f"Bedrock has no model '{BARE}'"):
        args.func(args)
    assert list(tmp_path.iterdir()) == []



def test_the_answerer_judge_and_generators_take_one_provider_list():
    # main's four copies of the tuple, merged; openai-codex stays out though their CLI offers it.
    assert CALLER_PROVIDERS == ("openai", "anthropic", "google", "gemini", "google-vertex", "openrouter",
                                "amazon-bedrock", "vertex_ai", *BACKENDS)
    for provider in ("openai-codex", "azure"):
        with pytest.raises(ValueError, match=f"Unsupported provider: '{provider}'"):
            check_caller_provider(provider)
