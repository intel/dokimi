# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The offline estimate of a routing run's calls and tokens (``novelty routing --estimate-only``)."""
from __future__ import annotations

from .routing import _SELECT_PROMPT, Candidate, tokenize

# Characters per input token of a selection prompt: measured ~2.6 on a 35-skill catalog billed by
# Bedrock (7,949 tokens a call), where chars / 4 gave 5,126, 1.55x low (issue #676).
SELECTION_CHARS_PER_TOKEN = 2.6
# Output tokens per selection call: the measured mean was 114 (Sonnet 5.5) to 193 (Haiku 5.5) in
# issue #676, 65 before. One flat figure in that range, as the repo holds no per-model table.
SELECTION_REPLY_TOKENS = 150


def selection_prompt_tokens(candidates: list[Candidate], queries: list[str]) -> list[int]:
    """The prompt tokens (chars / :data:`SELECTION_CHARS_PER_TOKEN`) of each call one repeat of
    :func:`dokimi.novelty.routing.model_routing` makes.

    One entry per sent query, in query order. ``model_routing`` sends every query, contentless
    ones too, unless none has a content word: then the run refuses before any call, and the list
    is empty. Each call carries the whole field's cards in the selection prompt.
    """
    cards = "\n".join(f"{position}. {candidate.card}"
                       for position, candidate in enumerate(candidates, 1))
    sent = list(queries) if any(tokenize(query) for query in queries) else []
    return [round(len(_SELECT_PROMPT.format(query=query, cards=cards)) / SELECTION_CHARS_PER_TOKEN)
            for query in sent]


def selection_estimate(candidates: list[Candidate], queries: list[str], *,
                       repeats: int = 1) -> dict:
    """The calls and tokens ``model_routing`` would make, counted without making them.

    Every repeat sends the same prompts (see :func:`selection_prompt_tokens`). Each reply is
    billed at :data:`SELECTION_REPLY_TOKENS` output tokens; ``output_tokens_per_call`` and
    ``chars_per_input_token`` say which allowances were used. Zero repeats send nothing, so no
    query is sent.
    """
    prompts = selection_prompt_tokens(candidates, queries) if repeats else []
    calls = len(prompts) * repeats
    return {
        "n_queries": len(queries),
        "n_queries_sent": len(prompts),
        "n_calls": calls,
        "input_tokens": sum(prompts) * repeats,
        "chars_per_input_token": SELECTION_CHARS_PER_TOKEN,
        "output_tokens_per_call": SELECTION_REPLY_TOKENS,
        "output_tokens": calls * SELECTION_REPLY_TOKENS,
    }
