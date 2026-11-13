# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Who answers each role of a novelty run, and what the run is expected to cost.

The command reads its flags; this module turns them into the (model, provider) of each role,
the three role clients, and the dollar estimate printed before a run. Command modules import
this module inside their functions, since importing it loads the novelty package.
"""

from __future__ import annotations

import argparse
import sys

from dokimi.utils import InputError


def role_models(args: argparse.Namespace) -> dict[str, tuple[str, str]]:
    """The (model, provider) of each role, defaulting so roles never silently collapse."""
    from dokimi.novelty.client import default_judge

    if args.judge_model:
        judge_model, judge_provider = args.judge_model, args.judge_provider or args.provider
    else:
        # a CLI answerer with no judge provider judges on the same CLI, another family
        judge_model, judge_provider = default_judge(args.model, args.judge_provider, args.provider)
    extract_model = args.extract_model or judge_model
    extract_provider = args.extract_provider or judge_provider
    return {
        "extractor": (extract_model, extract_provider),
        "answerer": (args.model, args.provider),
        "judge": (judge_model, judge_provider),
    }


def estimate_usd(
    args: argparse.Namespace, n_tokens: int, by_role: dict[str, int], tier: str | None,
    context_share: float = 0.5,
) -> float | None:
    """The estimate's dollar figure; ``None`` when a role's model cannot be resolved or priced.

    The reason goes to stderr: a role whose model does not resolve, or a role with no list
    price, makes the figure unavailable, and the note names it.
    """
    from dokimi.llm_pricing import list_cost_usd
    from dokimi.novelty.tiers import estimate_usd as tier_estimate_usd

    try:
        models = role_models(args)
    except ValueError as exc:
        print(f"The USD estimate is unavailable: {exc}", file=sys.stderr)
        return None
    usd = tier_estimate_usd(by_role, n_tokens, models, tier, context_share)
    if usd is None:
        unpriced = sorted(
            f"{role} ({model})"
            for role, (model, provider) in models.items()
            if list_cost_usd(model, provider, 1, 1) is None
        )
        print(
            "The USD estimate is unavailable: no list price in the litellm price map for "
            + ", ".join(unpriced),
            file=sys.stderr,
        )
    return usd


def clients(args: argparse.Namespace):
    """Build the three role clients, defaulting so roles never silently collapse."""
    try:
        models = role_models(args)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    return (
        client(*models["extractor"], "extractor", args),
        client(*models["answerer"], "answerer", args),
        client(*models["judge"], "judge", args),
    )


def spend_cap(args: argparse.Namespace):
    """The invocation's one ``--max-usd`` cap, shared by every role client."""
    from dokimi.novelty.client import SpendCap

    limit = getattr(args, "max_usd", None)
    if limit is None:
        return None
    if getattr(args, "_spend_cap", None) is None:
        args._spend_cap = SpendCap(limit_usd=limit)
    return args._spend_cap


def client(model: str, provider: str, role: str, args: argparse.Namespace):
    """One role's client, sharing the invocation's ``--max-usd`` cap."""
    from dokimi.novelty.client import LLMClient

    return LLMClient(
        model=model, provider=provider, role=role,
        max_tokens=getattr(args, "max_tokens", None),
        cap=spend_cap(args),
    )
