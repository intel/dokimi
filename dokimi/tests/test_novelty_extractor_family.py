# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Extractor vs judge: the one who writes the answer key should not be the one who grades it."""

from __future__ import annotations

import json

import pytest

from dokimi.commands.novelty_atoms_file import warn_answer_key
from dokimi.novelty.atoms import save_atoms
from dokimi.novelty.client import (
    LLMClient,
    extractor_judge_relation,
    vendor_of,
    warn_extractor_judge,
)


def _judge(model: str, provider: str = "anthropic") -> LLMClient:
    return LLMClient(model=model, provider=provider, role="judge")


def _relation(extractor_model: str, judge: LLMClient, extractor_provider: str = "anthropic"):
    return extractor_judge_relation(extractor_model, judge, extractor_provider)


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
    relation = _relation("claude-sonnet-5-5", _judge("claude-sonnet-5-5"))
    assert relation == {"extractor": "claude-sonnet-5-5", "judge": "claude-sonnet-5-5",
                        "same_model": True, "same_vendor": True}


def test_a_cli_alias_and_the_full_id_of_its_family_are_one_model():
    judge = _judge("claude-sonnet-5-5", provider="claude-cli")
    assert _relation("sonnet", judge, "claude-cli")["same_model"] is True


def test_another_model_of_the_same_vendor_is_flagged_as_same_vendor_only():
    relation = _relation("claude-haiku-5-5", _judge("claude-opus-5-5"))
    assert relation["same_model"] is False
    assert relation["same_vendor"] is True


def test_different_vendors_are_not_flagged():
    relation = _relation("claude-haiku-5-5", _judge("gpt-6.1-sol", provider="openai"))
    assert relation["same_model"] is False
    assert relation["same_vendor"] is False


def test_an_unknown_vendor_never_counts_as_the_same_vendor():
    assert _relation("local-a", _judge("local-b", provider="openai"))["same_vendor"] is False


def test_the_same_unrecognised_model_on_both_sides_is_the_same_vendor():
    assert _relation("my-local-model", _judge("my-local-model", provider="openai"))["same_vendor"] is True


def test_the_same_model_warns_on_stderr_and_names_the_model(capsys):
    relation = warn_extractor_judge("claude-sonnet-5-5", _judge("claude-sonnet-5-5"))
    err = capsys.readouterr().err
    assert relation["same_model"] is True
    assert "are both claude-sonnet-5-5" in err and "--extract-model" in err


def test_the_same_vendor_warns_with_the_vendor_family_wording(capsys):
    warn_extractor_judge("claude-haiku-5-5", _judge("claude-opus-5-5"))
    err = capsys.readouterr().err
    assert "WARNING" in err and "vendor family" in err


def test_a_cross_family_pair_prints_nothing(capsys):
    warn_extractor_judge("claude-haiku-5-5", _judge("gpt-6.1-sol", provider="openai"))
    assert capsys.readouterr().err == ""


def test_probe_compares_the_saved_key_writer_with_the_judge_and_a_cross_family_pair_is_silent(capsys):
    # the probe's own --extract-model is irrelevant: the key was written when the atoms were saved
    judge = _judge("claude-sonnet-5-5")
    warn_answer_key({"extractor": "gpt-6.1-sol"}, LLMClient(model="claude-opus-5-5"), judge)
    assert capsys.readouterr().err == ""


def test_probe_warns_when_the_judge_wrote_the_saved_answer_key(capsys):
    warn_answer_key({"extractor": "claude-sonnet-5-5"}, LLMClient(model="gpt-6.1-sol"),
                    _judge("claude-sonnet-5-5"))
    assert "are both claude-sonnet-5-5" in capsys.readouterr().err


def test_probe_still_warns_when_the_answerer_wrote_the_saved_answer_key(capsys):
    warn_answer_key({"extractor": "gpt-6.1-sol"}, LLMClient(model="gpt-6.1-sol"),
                    _judge("claude-sonnet-5-5"))
    assert "wrote its own answer key" in capsys.readouterr().err


def test_a_saved_answer_key_with_no_model_name_is_not_compared(capsys):
    warn_answer_key({"extractor": None}, LLMClient(model="gpt-6.1-sol"), _judge("claude-sonnet-5-5"))
    assert capsys.readouterr().err == ""


def test_a_key_saved_by_a_cli_alias_warns_as_the_same_model(capsys):
    # the saved record names the extractor's provider, so the alias is matched to its full id
    warn_answer_key({"extractor": "sonnet", "extractor_provider": "claude-cli"},
                    LLMClient(model="gpt-6.1-sol"), _judge("claude-sonnet-5-5"))
    assert "are both claude-sonnet-5-5" in capsys.readouterr().err


def test_an_older_key_naming_a_cli_alias_is_read_as_the_claude_cli_alias(capsys):
    # a record from before the provider was saved: a bare alias can only be the CLI's
    warn_answer_key({"extractor": "sonnet"}, LLMClient(model="gpt-6.1-sol"), _judge("claude-sonnet-5-5"))
    assert "are both claude-sonnet-5-5" in capsys.readouterr().err


def test_save_atoms_records_the_extractor_provider(tmp_path):
    path = tmp_path / "atoms.json"
    save_atoms([], [], path, extractor="sonnet", extractor_provider="claude-cli")
    assert json.loads(path.read_text(encoding="utf-8"))["extractor_provider"] == "claude-cli"


@pytest.mark.parametrize("extractor", [
    "anthropic/claude-sonnet-5-5",
    "us.anthropic.claude-sonnet-5-5-v1:0",
    "claude-sonnet-5-5[1m]",
])
def test_equivalent_spellings_of_one_api_model_are_the_same_model(extractor):
    relation = _relation(extractor, _judge("claude-sonnet-5-5"), "anthropic")
    assert relation["same_model"] is True
    assert relation["same_vendor"] is True


def test_the_bedrock_spelling_of_the_judge_matches_the_plain_extractor():
    relation = _relation("claude-sonnet-5-5", _judge("us.anthropic.claude-sonnet-5-5"), "anthropic")
    assert relation["same_model"] is True


def test_two_versions_of_one_family_are_not_the_same_model():
    relation = _relation("anthropic/claude-sonnet-5-5", _judge("claude-sonnet-5-6"), "anthropic")
    assert relation["same_model"] is False
    assert relation["same_vendor"] is True


@pytest.mark.parametrize("extractor, judge", [
    ("claude-sonnet-5-5", _judge("claude-sonnet-5-5")),
    ("claude-haiku-5-5", _judge("claude-opus-5-5")),
])
def test_the_warning_names_a_judge_change_or_a_rewritten_key_not_a_probe_flag(capsys, extractor, judge):
    # probe's --extract-model does not change a saved key, so the advice must not point at it alone
    warn_extractor_judge(extractor, judge)
    assert "a judge from another family" in capsys.readouterr().err


def test_an_older_key_naming_a_cli_alias_with_a_context_suffix_is_the_cli_alias(capsys):
    warn_answer_key({"extractor": "sonnet[1m]"}, LLMClient(model="gpt-6.1-sol"), _judge("claude-sonnet-5-5"))
    assert "are both claude-sonnet-5-5" in capsys.readouterr().err
