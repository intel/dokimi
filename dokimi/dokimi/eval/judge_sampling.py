# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The judge's sampling check, run before a run pays for anything."""

from __future__ import annotations

from typing import Optional

from dokimi import llm_cli
from dokimi.llm import sampling_refusals
from dokimi.llm_sampling import NAME_REFUSAL


def check_judge_sampling(provider: str, model: Optional[str], temperature: Optional[float],
                         seed: Optional[int]) -> None:
    """Raise :class:`llm_cli.SamplingError` before a run when a judge would be refused its sampling.

    A CLI provider takes no temperature or seed. An API route is also checked for the values it
    refuses (Haiku 5.5 on Bedrock takes only the default temperature), so the refusal comes before
    any judgement is paid for, not as one failed judgement per item.
    """
    llm_cli.check_sampling(provider, temperature, seed)
    if model is None or (temperature is None and seed is None) or llm_cli.backend(provider):
        return
    bad = sampling_refusals(model, provider, temperature=temperature, seed=seed)
    if bad:
        requested = {"temperature": temperature, "seed": seed}
        refused = ", ".join(f"{name}={requested[name]}" for name in bad)
        reasons = [reason for reason in bad.values() if reason != NAME_REFUSAL]
        why = "; ".join(reasons) or "the provider does not take this option"
        raise llm_cli.SamplingError(
            f"{provider} refuses {refused} for model '{model}'. "
            f"{why}. "
            "Use a supported value or omit the corresponding judge option to use the provider default.")
