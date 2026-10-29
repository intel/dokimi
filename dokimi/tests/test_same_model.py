# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One rule for "same model": the self-judge warning, `baseline compare` and `repo compare` all use it."""

from __future__ import annotations

import pytest

from dokimi.defaults import BEDROCK_MODEL_IDS
from dokimi.utils import same_model

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
]


@pytest.mark.parametrize("a, b, same", CASES)
def test_same_model_table(a, b, same):
    assert same_model(a, b) is same


@pytest.mark.parametrize("a, b, same", CASES)
def test_same_model_is_symmetric(a, b, same):
    assert same_model(b, a) is same


@pytest.mark.parametrize("bare, profile", sorted(BEDROCK_MODEL_IDS.items()))
def test_a_bedrock_alias_and_its_profile_are_one_model(bare, profile):
    assert same_model(bare, profile)
    assert same_model(f"amazon-bedrock/{bare}", f"amazon-bedrock/{profile}")
