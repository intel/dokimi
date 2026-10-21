# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Every judge provider the CLI offers is one Judge accepts.

``benchmark run/preflight/batch`` (and the arms, subjects and orchestrate
commands) offered ``openai-codex`` as a judge provider, which ``Judge`` rejects
with ValueError -- on ``benchmark run`` only after every answer had been paid for.
"""

from __future__ import annotations

import argparse

import pytest

from dokimi.cli import build_parser
from dokimi.eval.judge import JUDGE_PROVIDERS, Judge


def _judge_provider_actions(parser, path=()):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _judge_provider_actions(sub, path + (name,))
        elif action.dest in ("judge_provider", "escalate_provider") and action.choices:
            yield " ".join(path) + " " + action.option_strings[0], action.choices


#: Judge-role flags whose value never reaches ``Judge``: the novelty commands call
#: the model through ``novelty.client.LLMClient`` -> ``llm_call_with_usage``, which
#: does route ``openai-codex``.
NOT_THE_JUDGE_CLASS = {"novelty analyze --judge-provider", "novelty probe --judge-provider"}


def test_every_judge_provider_flag_offers_exactly_judge_providers():
    # Equality, not subset: a hardcoded list that merely omits a rejected
    # provider (as ``eval score`` had) still drifts from what Judge accepts.
    actions = dict(_judge_provider_actions(build_parser()))
    for flag in ("benchmark run --judge-provider", "arms run --judge-provider",
                 "arms matrix-run --judge-provider", "subjects run --judge-provider",
                 "evaluate --judge-provider", "eval score --judge-provider",
                 "eval judge-validity --escalate-provider"):
        assert flag in actions
    assert NOT_THE_JUDGE_CLASS <= actions.keys()
    for flag in NOT_THE_JUDGE_CLASS:
        del actions[flag]
    offenders = {
        flag: list(choices)
        for flag, choices in actions.items()
        if tuple(choices) != JUDGE_PROVIDERS
    }
    assert not offenders


def test_benchmark_run_rejects_openai_codex_judge_at_parse_time(capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(
            ["benchmark", "run", "--library", "x", "--judge-provider", "openai-codex"]
        )
    assert exc.value.code == 2
    assert "invalid choice: 'openai-codex'" in capsys.readouterr().err


# Together with the equality test above this covers `eval score` and every other flag.
@pytest.mark.parametrize("provider", JUDGE_PROVIDERS)
def test_every_listed_judge_provider_is_accepted_by_judge(provider):  # test-quality: ok must not raise (ValueError = unrouted)
    Judge(model="m", provider=provider, api_key="k")
