# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One rule for "same model": the self-judge warning, `baseline compare` and `repo compare` all use it."""

from __future__ import annotations

import pytest

from dokimi.defaults import BEDROCK_MODEL_IDS
from dokimi.utils import model_identity, same_model

# (a, b, same) with a route-qualified name ("provider/model") where the route is part of the input.
CASES = [
    ("claude-sonnet-4-5", "claude-sonnet-4-5", True),
    ("Claude-Sonnet-4-5", "claude-sonnet-4-5", True),
    (" claude-sonnet-4-5 ", "claude-sonnet-4-5", True),
    ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5", True),  # a snapshot and its undated alias
    ("claude-3-5-sonnet-20240620", "claude-3-5-sonnet-20241022", False),  # two snapshots
    ("claude-sonnet-4-5@20250929", "claude-sonnet-4-5-20250929", True),  # Vertex date form
    ("claude-sonnet-4-5", "claude-opus-4-5-20251101", False),
    ("claude-sonnet-4-5", "claude-sonnet-4-5-1", False),
    ("haiku", "claude-haiku-3", False),  # a family name is not a full id
    ("x.claude-haiku", "haiku", False),
    ("gpt-4.1", "1", False),  # a dot after a digit or dash is not a vendor prefix
    ("", "", False),  # an unset model is not evidence of sameness
    ("claude-sonnet-4-5", "", False),
    ("deepseek-v2", "deepseek-v3", False),  # a bare -v2 is part of the id
    ("us.anthropic.claude-sonnet-4-5", "eu.anthropic.claude-sonnet-4-5", True),  # regional profiles
    ("global.anthropic.claude-sonnet-4-5", "claude-sonnet-4-5", True),
    ("us.anthropic.claude-x", "eu.anthropic.claude-y", False),
    ("us.anthropic.claude-haiku-4-5-20251001-v1:0", "claude-haiku-4-5-20251001", True),
    ("eu.anthropic.claude-sonnet-4-5-20250929-v1:0", "us.anthropic.claude-sonnet-4-5", True),
    ("m-v1:0", "m-v3:0", True),  # Bedrock revision suffix
    ("openai.gpt-oss-120b-1:0", "gpt-oss-120b", True),  # a plain -N:M revision
    ("openai.gpt-oss-120b-1:0", "openai.gpt-oss-120b-2:0", True),
    ("openai.gpt-oss-120b-1:0", "gpt-oss-20b", False),
    ("claude-sonnet-4-5", "us.anthropic.claude-sonnet-4-5-20250929-v1:0", True),  # alias vs full Bedrock id
    ("claude-x-20250929", "claude-x-20241022", False),
    ("gpt-4o-2024-08-06", "gpt-4o", True),  # OpenAI's dashed snapshot and its alias
    ("gpt-4o-2024-08-06", "gpt-4o-2024-05-13", False),
    ("gpt-4o-2024-08-06", "gpt-4o-20240806", True),
    ("anthropic/claude-sonnet-4-5", "amazon-bedrock/claude-sonnet-4-5", True),  # same model, another route
    ("anthropic/claude-x", "claude-x", True),
    ("vertex_ai/claude-x", "claude-x", True),
    ("vertex_ai/claude-x@20250929", "vertex_ai/claude-x", True),
    ("bedrock/us.anthropic.claude-x", "us.anthropic.claude-x", True),
    ("amazon-bedrock/us.anthropic.claude-sonnet-4-5", "amazon-bedrock/eu.anthropic.claude-sonnet-4-5", True),
    ("openrouter/anthropic/claude-sonnet-4-5", "anthropic/claude-sonnet-4-5", True),
    ("anthropic/claude-sonnet-5-5", "anthropic/us.anthropic.claude-sonnet-5-5", True),
    # the organization is part of a Hugging Face style id; only a route is dropped
    ("meta-llama/Llama-3.1-8B-Instruct", "myorg/Llama-3.1-8B-Instruct", False),
    ("meta-llama/Llama-3.1-8B-Instruct", "meta-llama/Llama-3.1-8B-Instruct", True),
    ("openrouter/meta-llama/llama-3.1-8b-instruct", "meta-llama/Llama-3.1-8B-Instruct", True),
    ("openrouter/meta-llama/llama-3.1-8b-instruct", "openrouter/myorg/llama-3.1-8b-instruct", False),
    ("meta-llama/Llama-3.1-8B-Instruct", "Llama-3.1-8B-Instruct", False),
    ("arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-sonnet-4-5-20250929-v1:0",
     "claude-sonnet-4-5-20250929", True),
    ("bedrock/converse/openai.gpt-oss-120b", "openai/gpt-oss-120b", True),
    ("azure/gpt-4o", "gpt-4o", True),  # a litellm route that is not one of dokimi's providers
    ("groq/llama-3.1-8b", "llama-3.1-8b", True),
    ("together_ai/meta-llama/llama-3-8b", "meta-llama/llama-3-8b", True),
    ("models/gemini-2.0-flash", "gemini-2.0-flash", True),
    ("arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-20250929-v1:0",
     "claude-sonnet-4-5-20250929", True),
    # a custom deployment name with a dot is not a vendor prefix: only the vendors of Bedrock ids are dropped
    ("alice.model", "bob.model", False),
    ("alice.model", "model", False),
    ("alice.model", "alice.model", True),
    ("azure/alice.model", "azure/bob.model", False),
    # an explicit Hugging Face route is dropped, and the organization after it stays part of the id
    ("huggingface/google/gemma-2-9b-it", "gemma-2-9b-it", False),
    ("huggingface/google/gemma-2-9b-it", "huggingface/google/gemma-2-9b-it", True),
    ("huggingface/meta-llama/Llama-3.1-8B-Instruct", "meta-llama/llama-3.1-8b-instruct", True),
    ("huggingface/openai/gpt-oss-120b", "huggingface/myorg/gpt-oss-120b", False),
    ("huggingface/gemma-2-9b-it", "gemma-2-9b-it", True),
    # a route in front of the Hub route does not end the Hub guard: its organization stays too
    ("openrouter/huggingface/google/gemma-2-9b-it", "gemma-2-9b-it", False),
    ("openrouter/huggingface/google/gemma-2-9b-it", "huggingface/google/gemma-2-9b-it", True),
    ("zai.glm-4.7", "glm-4.7", True),  # a Bedrock vendor prefix is dropped whatever the vendor
    # OpenRouter writes the vendor as a path segment: one model on Bedrock and on OpenRouter
    ("qwen.qwen3-235b-a22b-2507-v1:0", "openrouter/qwen/qwen3-235b-a22b-2507", True),
    ("moonshotai.kimi-k2-thinking", "openrouter/moonshotai/kimi-k2-thinking", True),
    ("zai.glm-4.7", "openrouter/z-ai/glm-4.7", True),
    ("minimax.minimax-m2", "openrouter/minimax/minimax-m2", True),
    ("mistral.mistral-large", "openrouter/mistralai/mistral-large", True),
    ("openrouter/qwen/qwen3-8b", "openrouter/myorg/qwen3-8b", False),
    ("huggingface/qwen/qwen3-8b", "qwen3-8b", False),
]


@pytest.mark.parametrize("a, b, same", CASES)
def test_same_model_table(a, b, same):
    assert same_model(a, b) is same


@pytest.mark.parametrize("a, b, same", CASES)
def test_same_model_is_symmetric(a, b, same):
    assert same_model(b, a) is same


# The vendor prefixes of Bedrock model ids (the AWS model cards: zai.glm-4.7, xai.grok-4.6, moonshotai.kimi-k2.5, ...)
BEDROCK_VENDORS = [
    "ai21", "amazon", "anthropic", "cohere", "deepseek", "google", "luma", "meta", "minimax", "mistral",
    "moonshotai", "nvidia", "openai", "qwen", "stability", "twelvelabs", "writer", "xai", "zai",
]


@pytest.mark.parametrize("vendor", BEDROCK_VENDORS)
def test_a_bedrock_vendor_prefix_is_not_part_of_the_id(vendor):
    assert same_model(f"{vendor}.model-4.7", "model-4.7")
    assert same_model(f"{vendor}.model-4.7-v1:0", "model-4.7")


@pytest.mark.parametrize("bare, profile", sorted(BEDROCK_MODEL_IDS.items()))
def test_a_bedrock_alias_and_its_profile_are_one_model(bare, profile):
    assert same_model(bare, profile)
    assert same_model(f"amazon-bedrock/{bare}", f"amazon-bedrock/{profile}")


@pytest.mark.parametrize("a, b", [
    ("anthropic.", "openai."),  # a vendor prefix with no id after it
    ("anthropic.", "anthropic."),
    ("us.anthropic.", "us.anthropic."),
    ("openai/", "openai/"),  # a route with no id after it
    ("/", "/"),  # separators only
    (".", "."),
    ("eu./", "/"),  # a region profile with no id after it
    ("-", "-"),
])
def test_a_name_with_no_id_is_no_model(a, b):
    assert same_model(a, b) is False
    assert model_identity(a)[0] is None


def test_every_provider_dokimi_accepts_is_a_route_not_an_organization():
    from dokimi.llm import ANSWER_PROVIDERS, PROVIDER_ALIASES

    for provider in [*ANSWER_PROVIDERS, *PROVIDER_ALIASES]:  # a CLI backend too: a panel stores "claude-cli/<model>"
        assert same_model(f"{provider}/gpt-4o", "gpt-4o"), provider
