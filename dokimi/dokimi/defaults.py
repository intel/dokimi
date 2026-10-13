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

# Decision (2026-03-04): one fixed semantic retrieval config.
DEFAULT_RETRIEVAL_TOP_K = 3
DEFAULT_MAX_TOKENS_PER_QUESTION = 4000
DEFAULT_CONCURRENCY = 5
DEFAULT_RESULTS_SUFFIX = "_final"
