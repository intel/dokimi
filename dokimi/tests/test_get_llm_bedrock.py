# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Bedrock model ids: bare names map to an inference profile, real ids pass through."""

from __future__ import annotations

import pytest

from dokimi.defaults import DEFAULT_JUDGE_MODEL
from dokimi.llm import _build_litellm_model
from dokimi.utils import get_llm


@pytest.mark.parametrize("model", [
    "us.anthropic.claude-opus-4-1-20250805-v1:0",
    "anthropic.claude-3-haiku-20240307-v1:0",
    "amazon.nova-pro-v1:0",
])
def test_bedrock_model_id_is_not_replaced(monkeypatch, model):
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.sonnet")
    assert get_llm("amazon-bedrock", model).litellm_model == f"bedrock/{model}"


def test_bare_model_name_is_mapped_to_the_default_sonnet(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.sonnet")
    assert get_llm("amazon-bedrock", "claude-sonnet-4-5").litellm_model == "bedrock/us.anthropic.sonnet"


@pytest.mark.parametrize("bare, profile", [
    ("claude-sonnet-5-5", "us.anthropic.claude-sonnet-5-5"),
    ("claude-opus-5-5", "us.anthropic.claude-opus-5-5"),
    ("claude-haiku-5-5", "us.anthropic.claude-haiku-5-5"),
])
def test_a_known_bare_name_maps_to_its_own_profile_not_the_env_sonnet(monkeypatch, bare, profile):
    # A haiku must not silently become the box's default Sonnet.
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "us.anthropic.sonnet")
    assert get_llm("amazon-bedrock", bare).litellm_model == f"bedrock/{profile}"
    assert _build_litellm_model(bare, "amazon-bedrock") == f"bedrock/{profile}"


@pytest.mark.parametrize("bare, var", [
    ("claude-sonnet-5-5", "ANTHROPIC_DEFAULT_SONNET_MODEL"),
    ("claude-opus-5-5", "ANTHROPIC_DEFAULT_OPUS_MODEL"),
    ("claude-haiku-5-5", "ANTHROPIC_DEFAULT_HAIKU_MODEL"),
])
def test_the_env_profile_for_the_same_model_wins(monkeypatch, bare, var):
    # A run outside the US keeps its regional profile for the default names.
    monkeypatch.setenv(var, f"eu.anthropic.{bare}")
    assert get_llm("amazon-bedrock", bare).litellm_model == f"bedrock/eu.anthropic.{bare}"
    assert _build_litellm_model(bare, "amazon-bedrock") == f"bedrock/eu.anthropic.{bare}"


@pytest.mark.parametrize("override", ["us.anthropic.claude-sonnet-4-6", "claude-sonnet-5-5"])
def test_an_env_profile_for_another_model_or_not_a_bedrock_id_is_ignored(monkeypatch, override):
    monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", override)
    assert get_llm("amazon-bedrock", DEFAULT_JUDGE_MODEL).litellm_model == \
        f"bedrock/us.anthropic.{DEFAULT_JUDGE_MODEL}"


def test_the_default_judge_resolves_on_bedrock(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
    assert get_llm("amazon-bedrock", DEFAULT_JUDGE_MODEL).litellm_model.startswith("bedrock/us.anthropic.")


def test_the_answer_path_leaves_an_unknown_bare_name_alone():
    assert _build_litellm_model("some-model", "amazon-bedrock") == "bedrock/some-model"
    assert _build_litellm_model("us.anthropic.claude-sonnet-5-5", "amazon-bedrock") == \
        "bedrock/us.anthropic.claude-sonnet-5-5"
