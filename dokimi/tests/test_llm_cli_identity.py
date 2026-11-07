# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Model identity for the CLI provider: an alias and its full id are one model."""

from __future__ import annotations

import pytest

from dokimi import llm_cli
from dokimi.utils import same_model


def test_an_alias_and_its_full_id_are_one_model_for_the_identity_checks():
    same = same_model
    assert same("sonnet", "claude-sonnet-5-5")
    assert same("claude-sonnet-5-5", "sonnet")
    assert not same("sonnet", "claude-opus-5-5")
    assert not same("claude-sonnet-5-5", "claude-sonnet-4-5")
    assert not same("gpt-6.1-sol", "sonnet")


def test_a_full_id_behind_a_routing_prefix_is_the_same_model_as_the_bare_id():
    same = same_model
    assert same("claude-sonnet-5-5", "anthropic/claude-sonnet-5-5")
    assert same("claude-sonnet-5-5", "anthropic.claude-sonnet-5-5")
    assert not same("claude-sonnet-5-5", "anthropic/claude-sonnet-4-5")


def test_a_missing_model_is_not_compared_and_does_not_raise():
    assert not same_model(None, "claude-sonnet-5-5")
    assert not same_model("sonnet", None)


def test_the_one_million_context_form_is_a_claude_model_on_the_cli():
    # claude --model takes sonnet[1m]; it is the same model as the alias, with a larger window.
    assert llm_cli.accepts_model("claude-cli", "sonnet[1m]")
    assert llm_cli.accepts_model("claude-cli", "opus[1m]")
    assert llm_cli.accepts_model("claude-cli", "claude-sonnet-5-5[1m]")
    assert not llm_cli.accepts_model("claude-cli", "gpt-6.1-sol[1m]")
    assert llm_cli.family("sonnet[1m]") == "sonnet"
    assert same_model("sonnet[1m]", "claude-sonnet-5-5")
    assert not same_model("sonnet[1m]", "claude-opus-5-5")


def test_the_independence_warning_fires_for_an_alias_against_its_full_id(capsys):
    from dokimi.commands.evaluate import _warn_judge_independence

    assert _warn_judge_independence("claude-cli", "sonnet", "claude-cli", "claude-sonnet-5-5")
    assert "independence warning" in capsys.readouterr().err
    assert not _warn_judge_independence("claude-cli", "sonnet", "claude-cli", "claude-opus-5-5")


@pytest.mark.parametrize("answerer", ["sonnet", "opus", "claude-opus-5-1", "claude-sonnet-5-5"])
def test_the_novelty_default_judge_on_the_cli_is_another_family(answerer):
    from dokimi.novelty.client import default_judge

    judge, provider = default_judge(answerer, "claude-cli")
    assert provider == "claude-cli" and not same_model(judge, answerer)


def test_a_novelty_judge_of_the_answerers_family_counts_as_the_answerer():
    # run_measurement refuses a judge that same_client matches with the answerer, unless allowed.
    from dokimi.novelty.client import LLMClient, same_client

    judge = LLMClient(model="claude-sonnet-5-5", provider="anthropic")
    answerer = LLMClient(model="sonnet", provider="claude-cli")
    assert same_client(judge, answerer) and same_client(answerer, judge)
    assert not same_client(LLMClient(model="opus", provider="claude-cli"), answerer)


def test_an_alias_and_a_bedrock_or_prefixed_full_id_are_one_model_for_the_identity_checks():
    assert same_model("sonnet", "us.anthropic.claude-sonnet-5-5-v1:0")
    assert same_model("sonnet", "anthropic/claude-sonnet-5-5")
    assert not same_model("sonnet", "us.anthropic.claude-opus-5-1-v1:0")


def test_a_pinned_bedrock_full_id_is_the_same_model_as_its_bare_full_id():
    # The Bedrock revision suffix (-v1:0) is not part of the model's name; a different model is.
    same = same_model
    assert same("claude-sonnet-5-5", "us.anthropic.claude-sonnet-5-5-v1:0")
    assert same("us.anthropic.claude-sonnet-5-5-v2:0", "claude-sonnet-5-5")
    assert not same("claude-sonnet-5-5", "us.anthropic.claude-sonnet-5-6-v1:0")
    assert not same("claude-sonnet-5-5", "us.anthropic.claude-opus-5-5-v1:0")
    # Claude 2 ids carry their version before the revision: "claude-v2:1" is not a revision suffix.
    assert not same("claude-v2:1", "anthropic.claude-v2:0")


def test_the_independence_warning_fires_for_a_pinned_bedrock_id_of_the_cli_model(capsys):
    from dokimi.commands.evaluate import _warn_judge_independence

    assert _warn_judge_independence("claude-cli", "claude-sonnet-5-5", "amazon-bedrock",
                                    "us.anthropic.claude-sonnet-5-5-v1:0")
    assert "independence warning" in capsys.readouterr().err
