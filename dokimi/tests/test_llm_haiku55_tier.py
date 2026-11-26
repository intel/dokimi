# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Claude Haiku 5.5 prices a request by its prompt length: over 100,000 prompt tokens the whole request
pays the higher rates ($0.50 in / $2.50 out per MTok, from Anthropic's pricing page), and the US-only
route pays 1.1x on top. Cache reads and writes count toward the prompt length. litellm prices this
already; these tests pin it so a litellm upgrade or a route change cannot quietly drop the tier."""

from __future__ import annotations

import pytest
from litellm.types.utils import ModelResponse, Usage

from dokimi import llm


def _cost(litellm_model: str, *, prompt: int, completion: int = 1000, cache_read: int = 0) -> float:
    resp = ModelResponse(model=litellm_model, usage=Usage(
        prompt_tokens=prompt, completion_tokens=completion, cache_read_input_tokens=cache_read))
    cost = llm._safe_completion_cost(resp, litellm_model, litellm_model=litellm_model)
    assert cost is not None
    return cost


@pytest.mark.parametrize("prompt, expected", [
    (50_000, 50_000 * 1e-7 + 1000 * 5e-7),     # up to 100k: $0.10 in, $0.50 out
    (100_000, 100_000 * 1e-7 + 1000 * 5e-7),   # the threshold itself is still the lower tier
    (110_000, 110_000 * 5e-7 + 1000 * 2.5e-6),  # over 100k: the whole prompt at $0.50, out at $2.50
], ids=["under-100k", "at-100k", "over-100k"])
def test_first_party_haiku55_prices_the_whole_request_by_prompt_length(prompt, expected):
    assert _cost("anthropic/claude-haiku-5-5", prompt=prompt) == pytest.approx(expected, rel=1e-9)


def test_the_over_100k_tier_is_not_a_marginal_rate_on_the_excess():
    # A marginal reading would price 100k at $0.10 and only 10k at $0.50.
    whole = _cost("anthropic/claude-haiku-5-5", prompt=110_000)
    marginal = 100_000 * 1e-7 + 10_000 * 5e-7 + 1000 * 2.5e-6
    assert whole == pytest.approx(110_000 * 5e-7 + 1000 * 2.5e-6, rel=1e-9)
    assert whole > 3 * marginal  # 0.0575 against 0.0175


def test_cache_reads_count_toward_the_prompt_length():
    # 110k prompt of which 50k is cache reads: 60k fresh at $0.50, 50k cached at $0.05, out at $2.50.
    assert _cost("anthropic/claude-haiku-5-5", prompt=110_000, cache_read=50_000) == pytest.approx(
        60_000 * 5e-7 + 50_000 * 5e-8 + 1000 * 2.5e-6, rel=1e-9)


def test_the_us_only_route_pays_the_regional_premium_on_the_over_100k_tier():
    # inference_geo "us" is 1.1x on every category, the over-100k rates included.
    assert _cost("bedrock/us.anthropic.claude-haiku-5-5", prompt=110_000) == pytest.approx(
        (110_000 * 5e-7 + 1000 * 2.5e-6) * 1.1, rel=1e-9)
