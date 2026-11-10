# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A call with no credentials fails on its first attempt; a transient error is still retried.

litellm raises a missing key under a transient-looking class (``InternalServerError`` for
OpenAI, ``APIError`` for Azure, ``APIConnectionError`` for Gemini and Vertex, a missing project
or google-auth too), so it used to be retried three times, 2 + 4 + 8 s of backoff before the
same error. The OpenAI, Azure and Gemini cases run the production adapter: the client refuses
before it sends anything. The rest are the messages litellm, openai and google-auth raise,
on the class litellm wraps them in.
"""

import os

import litellm
import pytest

from dokimi import caching
from dokimi.llm import LLM, is_retryable

#: Every variable these routes read a key, token, endpoint or identity from (Azure's Entra ID too).
CREDENTIAL_ENV_PREFIXES = ("OPENAI_", "AZURE_", "GEMINI_", "GOOGLE_", "VERTEX")


@pytest.fixture
def sleeps(monkeypatch):
    slept: list = []
    monkeypatch.setattr("time.sleep", slept.append)
    monkeypatch.delenv(caching.RESPONSE_CACHE_ENV, raising=False)
    return slept


@pytest.fixture
def no_credentials(monkeypatch):
    """No key in the environment; the real ``litellm.completion`` runs, each call counted."""
    for var in [v for v in os.environ if v.startswith(CREDENTIAL_ENV_PREFIXES)]:
        monkeypatch.delenv(var)
    real, calls = litellm.completion, []

    def completion(**kwargs):
        calls.append(kwargs["model"])
        return real(**kwargs)

    monkeypatch.setattr(litellm, "completion", completion)
    return calls


@pytest.mark.parametrize("provider, model", [("openai", "gpt-4o"), ("azure", "gpt-4o"),
                                             ("google-vertex", "gemini/gemini-2.5-pro")])
def test_a_missing_key_fails_on_the_first_attempt(no_credentials, sleeps, provider, model):
    with pytest.raises(Exception, match="(?i)missing (gemini api key|credentials)|api_key client"):
        LLM(model, provider).ask("p")
    assert len(no_credentials) == 1 and sleeps == []


# As their sources word them: openai>=2.20 (the oldest litellm takes) and the current one,
# litellm's Vertex auth (no credentials, no project), google-auth's ADC, no google-auth.
MISSING_CREDENTIALS = [
    litellm.InternalServerError(
        "OpenAIException - The api_key client option must be set either by passing api_key to the "
        "client or by setting the OPENAI_API_KEY environment variable", "openai", "gpt-4o"),
    litellm.InternalServerError(
        "OpenAIException - Missing credentials. Please pass an `api_key`, `workload_identity`, "
        "`admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` environment variable.",
        "openai", "gpt-4o"),
    litellm.APIConnectionError(
        "Could not resolve credentials - either dynamically or from environment, for project_id: "
        "None", "vertex_ai", "gemini-2.5-pro"),
    litellm.APIConnectionError("Could not resolve project_id", "vertex_ai", "gemini-2.5-pro"),
    litellm.APIConnectionError(
        "Your default credentials were not found. To set up Application Default Credentials, see "
        "https://cloud.google.com/docs/authentication/external/set-up-adc for more information.",
        "vertex_ai", "gemini-2.5-pro"),
    litellm.APIConnectionError(
        "Google Cloud SDK not found. Install it with: pip install 'litellm[google]' or pip "
        "install google-cloud-aiplatform", "vertex_ai", "gemini-2.5-pro"),
]


#: The caller each error comes back to: litellm's provider name -> (model, dokimi provider).
ROUTES = {"openai": ("gpt-4o", "openai"), "azure": ("gpt-4o", "azure"),
          "vertex_ai": ("gemini-2.5-pro", "google-vertex")}


@pytest.mark.parametrize("error", MISSING_CREDENTIALS, ids=lambda e: str(e)[:48])
def test_a_credential_error_under_a_transient_class_is_not_retried(error, fake_llm, sleeps):
    fake_llm.push(error, "ok")
    with pytest.raises(type(error)):
        LLM(*ROUTES[error.llm_provider]).ask("p")
    assert len(fake_llm.requests) == 1 and sleeps == []


TRANSIENT = [
    litellm.RateLimitError("OpenAIException - Rate limit reached for gpt-4o", "openai", "gpt-4o"),
    litellm.ServiceUnavailableError("OpenAIException - Error code: 503", "openai", "gpt-4o"),
    litellm.InternalServerError("OpenAIException - The server had an error while processing your "
                                "request. Sorry about that!", "openai", "gpt-4o"),
    litellm.APIConnectionError("Connection error.", "vertex_ai", "gemini-2.5-pro"),
    litellm.APIError(502, "AzureException APIError - Bad gateway", "azure", "gpt-4o"),
]


@pytest.mark.parametrize("error", TRANSIENT, ids=lambda e: type(e).__name__)
def test_a_transient_error_is_still_retried(error, fake_llm, sleeps):
    fake_llm.push(error, error, "ok")
    assert LLM(*ROUTES[error.llm_provider]).ask("p") == "ok"
    assert len(fake_llm.requests) == 3 and sleeps == [2.0, 4.0]


def test_a_credential_word_in_an_explicit_retryable_flag_does_not_override_it():
    error = RuntimeError("missing credentials, but the caller knows better")
    error.retryable = True  # type: ignore[attr-defined]
    assert is_retryable(error)


#: litellm's classes that are transient whatever their text says (only the catch-alls
#: InternalServerError, APIError and APIConnectionError also carry a missing key).
TRANSIENT_ONLY = [
    lambda text: litellm.RateLimitError(f"Rate limit reached; {text}", "openai", "gpt-4o"),
    lambda text: litellm.ServiceUnavailableError(f"Error code: 503; {text}", "openai", "gpt-4o"),
    lambda text: litellm.Timeout(f"Request timed out; {text}", "gpt-4o", "openai"),
]


@pytest.mark.parametrize("marker", ["Missing credentials", "The api_key client option must be set",
                                    "Missing Gemini API key", "Could not resolve credentials",
                                    "Could not resolve project_id",
                                    "Your default credentials were not found",
                                    "Google Cloud SDK not found"])
@pytest.mark.parametrize("make", TRANSIENT_ONLY,
                         ids=["RateLimitError", "ServiceUnavailableError", "Timeout"])
def test_a_rate_limit_or_outage_echoing_a_credential_message_is_still_retried(make, marker,
                                                                              fake_llm, sleeps):
    error = make(marker)
    fake_llm.push(error, "ok")
    assert LLM("gpt-4o", "openai").ask("p") == "ok"
    assert len(fake_llm.requests) == 2 and sleeps == [2.0]
