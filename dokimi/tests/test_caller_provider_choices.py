# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A ``--provider`` whose command builds the answerer or a generator offers what they take (#404).

``answers generate``, ``eval questions generate``, ``eval personas discover``, ``evaluate`` and
``benchmark run|preflight|batch`` offered ``openai-codex``, which ``Answerer``,
``QuestionGenerator`` and ``PersonaGenerator`` refuse: the run stopped on a traceback, after
``evaluate`` had made its output directories, and ``benchmark preflight`` passed a plan that
``benchmark run`` refuses. The other ``--provider`` flags call :mod:`dokimi.llm` directly, which
routes ``openai-codex``, so they keep it.
"""

from __future__ import annotations

import argparse

import pytest

from dokimi.cli import build_parser
from dokimi.eval.answerer import Answerer
from dokimi.llm import ANSWER_PROVIDERS, CALLER_CHOICES
from dokimi.personas.generator import PersonaGenerator
from dokimi.questions.llm_gen import QuestionGenerator

#: The commands whose ``--provider`` reaches ``Answerer``, ``QuestionGenerator`` or
#: ``PersonaGenerator`` (``evaluate`` and ``benchmark`` through ``EvaluationPipeline``).
CALLER_FLAGS = [
    "answers generate", "eval questions generate", "eval personas discover", "evaluate",
    "benchmark run", "benchmark preflight", "benchmark batch",
]

#: Commands whose ``--provider`` goes to :mod:`dokimi.llm` itself (``llm_call``, ``run_session``,
#: ``chat_completion``, the novelty client).
LLM_FLAGS = [
    "eval questions analyze", "eval questions review", "eval score panel", "arms run",
    "arms matrix-run", "subjects run", "skill novelty analyze", "skill novelty atoms",
    "skill novelty probe", "skill shorten", "novelty routing", "tools preference",
]


def _provider_choices(parser, path=()):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _provider_choices(sub, path + (name,))
        elif action.dest == "provider" and action.choices:
            yield " ".join(path), tuple(action.choices)


def test_every_provider_flag_is_one_of_the_two_kinds():
    # A new --provider has to be put in a list: one that builds a caller needs CALLER_CHOICES.
    assert sorted(dict(_provider_choices(build_parser()))) == sorted(CALLER_FLAGS + LLM_FLAGS)


def test_the_commands_that_build_a_caller_offer_only_what_it_takes():
    choices = dict(_provider_choices(build_parser()))
    assert {flag: choices[flag] for flag in CALLER_FLAGS} == dict.fromkeys(CALLER_FLAGS, CALLER_CHOICES)
    assert set(ANSWER_PROVIDERS) - set(CALLER_CHOICES) == {"openai-codex"}


def test_the_commands_that_call_dokimi_llm_keep_openai_codex():
    choices = dict(_provider_choices(build_parser()))
    assert [flag for flag in LLM_FLAGS if "openai-codex" not in choices[flag]] == []


@pytest.mark.parametrize("argv", [
    ["answers", "generate", "--product", "p", "--questions", "q.json"],
    ["eval", "questions", "generate", "--product", "p", "--personas", "p.json"],
    ["eval", "personas", "discover", "--product", "p"],
    ["evaluate", "--product", "p"],
    ["benchmark", "run", "--library", "x"],
    ["benchmark", "preflight", "--library", "x"],
    ["benchmark", "batch", "--all"],
])
def test_openai_codex_is_refused_before_the_run(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args([*argv, "--provider", "openai-codex"])
    assert exc.value.code == 2
    assert "invalid choice: 'openai-codex'" in capsys.readouterr().err


CALLERS = {
    "answerer": lambda provider: Answerer(mcp_client=None, model="m", provider=provider, api_key="k"),
    "personas": lambda provider: PersonaGenerator("m", provider, "k"),
    "questions": lambda provider: QuestionGenerator("m", provider, "k"),
}


@pytest.mark.parametrize("caller", CALLERS)
@pytest.mark.parametrize("provider", CALLER_CHOICES)
def test_every_offered_provider_is_taken(caller, provider):  # test-quality: ok must not raise (ValueError = refused)
    CALLERS[caller](provider)
