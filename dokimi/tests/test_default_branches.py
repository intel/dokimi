# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Branches of the default-model change that the other tests leave unpinned."""

from __future__ import annotations

import pytest

from dokimi.cli import build_parser
from dokimi.novelty.client import default_judge
from dokimi.defaults import (
    DEFAULT_ANSWER_PROVIDER, DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER,
    DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER,
)
from dokimi.utils import bedrock_litellm_model


def test_routing_provider_default_is_the_answer_provider():
    a = build_parser().parse_args(["novelty", "routing", "--artifact", "skill:x", "--competitors", "skill:y"])
    assert a.provider == DEFAULT_ANSWER_PROVIDER


def test_tools_preference_provider_default_is_the_answer_provider():
    a = build_parser().parse_args(["tools", "preference", "--groups", "g.json", "--model", "m"])
    assert a.provider == DEFAULT_ANSWER_PROVIDER


@pytest.mark.parametrize("measured", [
    "anthropic/claude-sonnet-5-5", "openrouter/anthropic/claude-sonnet-5-5", "us.anthropic.claude-sonnet-5-5",
])
def test_measuring_the_default_judge_under_any_prefix_moves_the_judge(measured):
    assert default_judge(measured) == (DEFAULT_GENERATOR_MODEL, DEFAULT_GENERATOR_PROVIDER)


def test_other_models_keep_the_default_judge():
    assert default_judge("gpt-6.1-sol") == (DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER)


def test_a_dotless_arn_is_never_rewritten(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.claude-sonnet-X")
    arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc"
    assert bedrock_litellm_model(arn) == "bedrock/" + arn

