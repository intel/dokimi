# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""List-price estimates for a call that has not been made (kept out of ``dokimi.llm``)."""

from __future__ import annotations

import contextlib
import sys


def list_cost_usd(model: str, provider: str, prompt_tokens: int,
                  completion_tokens: int) -> float | None:
    """Dollar cost of one call of these token counts at list price, or ``None``.

    The price map :func:`dokimi.llm._safe_completion_cost` reads, for a call that has not
    been made: an estimate prices a token profile rather than a response. ``None`` when the
    model has no entry, for the same reason as there.
    """
    from litellm import cost_per_token

    from dokimi.llm import _has_price_entry, resolve_model

    litellm_model = resolve_model(model, provider)
    # litellm prints a "Provider List" help banner to stdout before it raises for a model
    # it cannot route. ``--estimate-only`` prints JSON on stdout, so keep that out of it.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            prompt, completion = cost_per_token(model=litellm_model, prompt_tokens=prompt_tokens,
                                                completion_tokens=completion_tokens)
        except Exception:
            return None
        if prompt + completion == 0.0 and not _has_price_entry(litellm_model, model):
            return None
    return float(prompt + completion)
