# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A judge or extractor of the answering model's vendor family is warned about, never refused.

Same-model pairs keep their own wording and their stored flag; a different model of one family
gets the family wording; another family, or a model with no known vendor, prints nothing.
"""

from dokimi.novelty import provenance
from dokimi.novelty.client import LLMClient, extractor_judge_relation
from dokimi.utils import UNKNOWN_VENDOR, vendor_of, warn_judge_independence


def test_provenance_reads_the_one_vendor_table_in_utils():
    assert provenance.vendor_of is vendor_of
    assert provenance.UNKNOWN_VENDOR == UNKNOWN_VENDOR == "unknown"


def test_a_different_model_of_the_answering_vendor_warns_with_the_family_wording(capsys):
    same = warn_judge_independence("amazon-bedrock", "claude-sonnet-5-5", "amazon-bedrock",
                                   "us.anthropic.claude-haiku-5-5", context="judged run")
    err = capsys.readouterr().err
    assert same is False  # the stored flag still means the same model
    assert "one anthropic family" in err and "judged run" in err
    assert "equals" not in err


def test_the_same_model_keeps_its_wording_and_its_flag(capsys):
    same = warn_judge_independence("openai", "gpt-6.1-sol", "openai", "gpt-6.1-sol", context="judged run")
    err = capsys.readouterr().err
    assert same is True
    assert "equals" in err and "family" not in err


def test_another_family_and_an_unknown_vendor_print_nothing(capsys):
    assert warn_judge_independence("openai", "gpt-6.1-sol", "amazon-bedrock", "claude-haiku-5-5") is False
    assert warn_judge_independence("local", "mymodel-a", "local", "mymodel-b") is False
    assert capsys.readouterr().err == ""


def test_routing_prefixes_do_not_determine_the_model_vendor(capsys):
    model = "openai/meta-llama/Llama-3.1-8B"
    assert vendor_of(model) == "meta"
    assert vendor_of("openrouter/openai/my-model") == "openai"
    assert vendor_of("openrouter/my-local-model") == UNKNOWN_VENDOR
    assert vendor_of("openai/community/my-local-model") == UNKNOWN_VENDOR
    assert not warn_judge_independence("openai", model, "openai", "llama-4-maverick")
    assert "one meta family" in capsys.readouterr().err
    assert not warn_judge_independence("openai", model, "openai", "gpt-6.1-sol")
    assert capsys.readouterr().err == ""
    assert extractor_judge_relation(model, LLMClient(model="llama-4-maverick", role="judge"))["same_vendor"]
    assert not extractor_judge_relation(model, LLMClient(model="gpt-6.1-sol", role="judge"))["same_vendor"]


def test_a_vendor_route_supplies_the_family_when_the_bare_id_does_not(capsys):
    assert vendor_of("openai/o3") == "openai"
    assert vendor_of("openai/o4-mini") == "openai"
    assert vendor_of("openai-codex/o3") == "openai"
    assert vendor_of("google/gemma-3-27b-it") == "google"
    assert vendor_of("gemini/gemma-3-27b-it") == "google"
    assert vendor_of("google-vertex/gemma-3-27b-it") == "google"
    assert vendor_of("mistral/codestral-latest") == "mistral"
    assert vendor_of("openrouter/deepseek/deepseek-r1") == "deepseek"
    assert not warn_judge_independence("openai", "openai/gpt-6.1-sol", "openai", "openai/o3")
    assert "one openai family" in capsys.readouterr().err
    relation = extractor_judge_relation("mistral/mistral-large", LLMClient(model="mistral/codestral-latest", role="judge"))
    assert relation["same_vendor"]


def test_an_extractor_of_the_answering_vendor_is_warned_and_another_vendor_is_not():
    warning = provenance.extractor_vendor_warning("claude-haiku-5-5", "claude-sonnet-5-5")
    assert warning and "both anthropic models" in warning and "--extract-model" in warning
    assert provenance.extractor_vendor_warning("gpt-6.1-sol", "claude-sonnet-5-5") is None
    assert provenance.extractor_vendor_warning("mymodel-a", "mymodel-b") is None
