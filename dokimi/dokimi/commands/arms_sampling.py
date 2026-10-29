# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Sampling-flag checks for the arms commands: a static refusal, and one real probe call."""

from __future__ import annotations

import argparse
import functools


def _check_sampling_support(args: argparse.Namespace) -> None:
    """Reject sampling flags the answer provider would refuse on every answer."""
    from dokimi.llm import unsupported_sampling_params

    bad = unsupported_sampling_params(
        args.model, args.provider,
        temperature=getattr(args, "temperature", None),
        seed=getattr(args, "seed", None),
    )
    if bad:
        raise ValueError(
            f"Provider '{args.provider}' does not support {', '.join(bad)} for "
            f"model '{args.model}'. Drop the flag(s); every answer would "
            "otherwise fail and the report would show null scores."
        )


@functools.lru_cache(maxsize=None)
def _probe_sampling(model: str, provider: str, temperature, seed) -> None:
    """One short real call with the requested sampling, so a value the provider
    rejects (gpt-5 accepts only temperature=1) fails before the run, not per answer."""
    if temperature is None and seed is None:
        return
    from dokimi.llm import llm_call_with_usage

    try:
        llm_call_with_usage("Reply with OK.", model=model, provider=provider,
                            max_tokens=16, temperature=temperature, seed=seed)
    except Exception as exc:
        raise ValueError(
            f"Probe call to '{provider}/{model}' with temperature={temperature}, "
            f"seed={seed} failed, so every call in the run would too: {exc}"
        ) from exc
