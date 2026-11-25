# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import pytest

from dokimi.cli import parse_args


@pytest.mark.parametrize(("argv", "option", "first", "last", "dests"), [
    (["eval", "score", "pointwise", "--answers", "a.json", "--product", "p"],
     "--judge", "openai/gpt-4o", "claude-sonnet-5-5", ("judge_model", "judge_provider", "judge_provider_named")),
    (["eval", "score", "pairwise", "--answers", "a.json"],
     "--judge", "openai/gpt-4o", "claude-sonnet-5-5", ("judge_model", "judge_provider", "judge_provider_named")),
    (["benchmark", "run", "--library", "onetbb"],
     "--model", "anthropic/claude-opus-5-5", "gpt-6.1-sol", ("model", "provider")),
    (["tools", "preference", "--groups", "groups.json"],
     "--model", "anthropic/claude-haiku-5-5", "gpt-6.1-sol", ("model", "provider")),
    (["skill", "novelty", "analyze", "--artifact", "skill:s"],
     "--model", "anthropic/claude-haiku-5-5", "gpt-4o", ("model", "provider")),
    (["skill", "novelty", "analyze", "--artifact", "skill:s"],
     "--extract-model", "openai/gpt-4o", "claude-haiku-5-5", ("extract_model", "extract_provider")),
])
def test_a_later_bare_reference_uses_the_leaf_defaults(argv, option, first, last, dests):
    only = parse_args([*argv, option, last])
    repeated = parse_args([*argv, option, first, option, last])
    assert tuple(getattr(repeated, dest) for dest in dests) == tuple(getattr(only, dest) for dest in dests)


@pytest.mark.parametrize(("argv", "option", "ref", "message"), [
    (["answers", "generate", "--product", "p", "--questions", "q.json"],
     "--model", "google/gemini-2.5-pro", "`google` is not a provider for this command"),
    (["tools", "preference", "--groups", "groups.json"],
     "--model", "claude-cli/", "`claude-cli` is not a provider for this command"),
    (["tools", "preference", "--groups", "groups.json"],
     "--model", "openai/ ", "--model openai/ has no model"),
    (["eval", "score", "pointwise", "--answers", "a.json", "--product", "p"],
     "--judge", "openai/ ", "--judge openai/ has no model"),
    (["skill", "novelty", "analyze", "--artifact", "skill:s"],
     "--extract-model", "anthropic/", "--extract-model anthropic/ has no model"),
    (["skill", "novelty", "probe", "--artifact", "skill:s", "--atoms", "atoms.json"],
     "--extract-model", "openai/ ", "--extract-model openai/ has no model"),
    (["catalog", "novelty", "--root", "catalog"],
     "--extract-model", "openai/", "--extract-model openai/ has no model"),
])
def test_malformed_qualified_references_fail_at_parse(argv, option, ref, message, capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args([*argv, option, ref])
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
