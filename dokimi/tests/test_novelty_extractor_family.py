# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Extractor vs judge: the one who writes the answer key should not be the one who grades it."""

from __future__ import annotations

import pytest

from dokimi.novelty.client import (
    LLMClient,
    extractor_judge_relation,
    vendor_of,
    warn_extractor_judge,
)


def _pair(extractor_model: str, judge_model: str, judge_provider: str = "anthropic"):
    return (LLMClient(model=extractor_model, provider="anthropic", role="extractor"),
            LLMClient(model=judge_model, provider=judge_provider, role="judge"))


@pytest.mark.parametrize("model, vendor", [
    ("us.anthropic.claude-haiku-5-5", "anthropic"),
    ("anthropic/claude-sonnet-5-5", "anthropic"),
    ("sonnet", "anthropic"),
    ("gpt-6.1-sol", "openai"),
    ("openai/gpt-6.1-sol", "openai"),
    ("gemini-3-pro", "google"),
    ("my-local-model", None),
    (None, None),
])
def test_vendor_is_read_off_the_model_id(model, vendor):
    assert vendor_of(model) == vendor


def test_the_same_model_is_reported_as_the_same_model_and_vendor():
    extractor, judge = _pair("claude-sonnet-5-5", "claude-sonnet-5-5")
    relation = extractor_judge_relation(extractor, judge)
    assert relation == {"extractor": "claude-sonnet-5-5", "judge": "claude-sonnet-5-5",
                        "same_model": True, "same_vendor": True}


def test_a_cli_alias_and_the_full_id_of_its_family_are_one_model():
    extractor = LLMClient(model="sonnet", provider="claude-cli", role="extractor")
    judge = LLMClient(model="claude-sonnet-5-5", provider="claude-cli", role="judge")
    assert extractor_judge_relation(extractor, judge)["same_model"] is True


def test_another_model_of_the_same_vendor_is_flagged_as_same_vendor_only():
    extractor, judge = _pair("claude-haiku-5-5", "claude-opus-5-5")
    relation = extractor_judge_relation(extractor, judge)
    assert relation["same_model"] is False
    assert relation["same_vendor"] is True


def test_different_vendors_are_not_flagged():
    extractor, judge = _pair("claude-haiku-5-5", "gpt-6.1-sol", judge_provider="openai")
    relation = extractor_judge_relation(extractor, judge)
    assert relation["same_model"] is False
    assert relation["same_vendor"] is False


def test_an_unknown_vendor_never_counts_as_the_same_vendor():
    extractor, judge = _pair("local-a", "local-b", judge_provider="openai")
    assert extractor_judge_relation(extractor, judge)["same_vendor"] is False


def test_the_same_model_warns_on_stderr_and_names_the_model(capsys):
    extractor, judge = _pair("claude-sonnet-5-5", "claude-sonnet-5-5")
    relation = warn_extractor_judge(extractor, judge)
    err = capsys.readouterr().err
    assert relation["same_model"] is True
    assert "are both claude-sonnet-5-5" in err and "--extract-model" in err


def test_the_same_vendor_warns_with_the_vendor_family_wording(capsys):
    extractor, judge = _pair("claude-haiku-5-5", "claude-opus-5-5")
    warn_extractor_judge(extractor, judge)
    err = capsys.readouterr().err
    assert "WARNING" in err and "vendor family" in err


def test_a_cross_family_pair_prints_nothing(capsys):
    extractor, judge = _pair("claude-haiku-5-5", "gpt-6.1-sol", judge_provider="openai")
    warn_extractor_judge(extractor, judge)
    assert capsys.readouterr().err == ""
