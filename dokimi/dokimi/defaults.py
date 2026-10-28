# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Central defaults for benchmark runs.

Keep user-facing CLI defaults in one place so docs, commands, and tests do not
quietly drift. Model names are LiteLLM/provider-facing strings.

Every surface — `benchmark`, `answers`, `eval`, `arms`, `subjects`, `evaluate`,
`questions`, `personas`, `novelty` and the library classes behind them — takes
its answerer, judge and generator from the three pairs below, so a delta taken
with default flags on one surface was answered and scored by the same models as
one taken on another. The judge is a different family from the answerer, which
keeps the judge-independence warning quiet.

Changed 2026-10-13, when several surfaces still defaulted to an obsolete small
OpenAI model and the rest to gpt-5.5 + claude-sonnet-4-5: results taken with
default flags before that are not comparable with ones taken after; re-baseline
(docs/benchmarking-and-comparison.md, "Default models").
"""

import os

DEFAULT_ANSWER_PROVIDER = "openai"
DEFAULT_ANSWER_MODEL = "gpt-6.1-sol"
DEFAULT_JUDGE_PROVIDER = "anthropic"
DEFAULT_JUDGE_MODEL = "claude-sonnet-5-5"
DEFAULT_GENERATOR_PROVIDER = "openai"
DEFAULT_GENERATOR_MODEL = "gpt-6.1-sol"

#: Bare Anthropic model names -> the Bedrock cross-region inference-profile ids they
#: run under. Bedrock rejects a bare ``claude-*`` name, so without this the default
#: judge fails on ``--provider amazon-bedrock``. A name that already is a Bedrock id
#: (it has a dot: ``us.anthropic.``, ``anthropic.``, ``amazon.``) or an ARN is never
#: looked up.
BEDROCK_MODEL_IDS = {
    "claude-opus-5-5": "us.anthropic.claude-opus-5-5",
    "claude-sonnet-5-5": "us.anthropic.claude-sonnet-5-5",
    "claude-haiku-5-5": "us.anthropic.claude-haiku-5-5",
}


def bedrock_profile(model: str) -> str | None:
    """The Bedrock id a bare name in ``BEDROCK_MODEL_IDS`` runs under, else ``None``.

    The family's ``ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU}_MODEL`` wins when it names the
    same model under another profile (``eu.anthropic.claude-sonnet-5-5``), so a run
    outside the US can keep its regional profile. One naming another model (a box pinned
    to an older Sonnet) is ignored: it would silently swap the model, the judge included.
    """
    profile = BEDROCK_MODEL_IDS.get(model)
    if profile is None:
        return None
    family = model.split("-")[1].upper()
    override = os.environ.get(f"ANTHROPIC_DEFAULT_{family}_MODEL", "")
    if "." in override and override.rsplit("anthropic.", 1)[-1].startswith(model):
        return override
    return profile



def default_judge_model(provider: str) -> str | None:
    """The default judge model that runs on ``provider``; ``None`` when none does.

    The default judge runs on its own provider and, by its inference profile, on
    ``amazon-bedrock``; on the default generator's provider the generator is the pick.
    Pairing a provider with another provider's model (``openai/claude-sonnet-5-5``)
    fails at the first call, so a command with no default here asks for ``--judge-model``.
    """
    if provider == DEFAULT_JUDGE_PROVIDER or (provider == "amazon-bedrock"
                                              and DEFAULT_JUDGE_MODEL in BEDROCK_MODEL_IDS):
        return DEFAULT_JUDGE_MODEL
    if provider == DEFAULT_GENERATOR_PROVIDER:
        return DEFAULT_GENERATOR_MODEL
    return None


class _JudgeModelForProvider(str):
    """``--judge-model`` left out: ``dokimi.cli.parse_args`` puts the provider's default in its place."""


#: The ``--judge-model`` default of every command that also takes ``--judge-provider``
#: (``novelty`` picks its own). Code that builds its arguments without
#: ``dokimi.cli.parse_args`` reads it as ``DEFAULT_JUDGE_MODEL``.
JUDGE_MODEL_FOR_PROVIDER = _JudgeModelForProvider(DEFAULT_JUDGE_MODEL)
JUDGE_MODEL_HELP = (f"Judge model (default: the default judge for --judge-provider: "
                    f"{DEFAULT_JUDGE_MODEL} on {DEFAULT_JUDGE_PROVIDER} and amazon-bedrock, "
                    f"{DEFAULT_GENERATOR_MODEL} on {DEFAULT_GENERATOR_PROVIDER}; other providers "
                    f"need this flag)")

# Decision (2026-03-04): one fixed semantic retrieval config.
DEFAULT_RETRIEVAL_TOP_K = 3

# Version of the answering and judging method, stamped in the answers and eval artifacts.
# Bump it when a change moves scores, so that compare can refuse runs of different versions.
# An artifact without the stamp predates it (#228 step 2); treat it as version 1.
ANSWERING_METHOD_VERSION = 2
DEFAULT_MAX_TOKENS_PER_QUESTION = 4000
DEFAULT_CONCURRENCY = 5
DEFAULT_RESULTS_SUFFIX = "_final"
