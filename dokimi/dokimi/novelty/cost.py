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


def judge_refs(args: argparse.Namespace) -> list[tuple[str, str]]:
    """The ``--judge PROVIDER/MODEL`` list as (model, provider) pairs; empty without the flag."""
    from dokimi.llm import ANSWER_PROVIDERS as PROVIDERS
    from dokimi.utils import same_model

    refs = getattr(args, "judges", None) or []
    if refs and (getattr(args, "judge_model", None) or getattr(args, "judge_provider", None)):
        raise ValueError("--judge replaces --judge-model and --judge-provider; pass one form")
    out: list[tuple[str, str]] = []
    for ref in refs:
        provider, sep, model = ref.partition("/")
        if not sep or not model or provider not in PROVIDERS:
            raise ValueError(f"--judge {ref!r} is not PROVIDER/MODEL with PROVIDER one of "
                             f"{', '.join(PROVIDERS)}")
        if any(same_model(m, model) for m, _ in out):
            # one model would vote twice, and the panel keys each judge's verdicts by model name
            raise ValueError(f"--judge {ref!r}: model {model!r} is given twice")
        out.append((model, provider))
    return out


def add_panel_usd(args: argparse.Namespace, usd, by_role, n_tokens, tier, context_share, models):
    """The USD estimate with each panel judge after the first grading every answer again."""
    from dokimi.novelty.tiers import estimate_usd

    for model, provider in judge_refs(args)[1:]:
        if usd is None:
            break
        extra = estimate_usd({"judge": by_role.get("judge", 0)}, n_tokens,
                             {"judge": (model, provider)}, tier, context_share)
        if extra is None:
            models[f"judge {model}"] = (model, provider)
        usd = None if extra is None else round(usd + extra, 6)
    return usd


def role_models(args: argparse.Namespace) -> dict[str, tuple[str, str]]:
    """The (model, provider) of each role, defaulting so roles never silently collapse."""
    from dokimi.novelty.client import default_judge

    panel = judge_refs(args)
    if panel:
        judge_model, judge_provider = panel[0]
    elif args.judge_model:
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
    """The estimate's dollar figure; ``None`` when a role's model cannot be priced.

    The reason goes to stderr: a role whose model does not resolve, or a role with no list
    price, makes the figure unavailable, and the note names it. Each panel judge after the
    first is priced as grading every answer again.

    Raises:
        InputError: A ``--judge`` that is not a usable PROVIDER/MODEL, as the run itself refuses it.
    """
    from dokimi.llm_pricing import list_cost_usd
    from dokimi.novelty.tiers import estimate_usd as tier_estimate_usd

    try:
        judge_refs(args)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    try:
        models = role_models(args)
    except ValueError as exc:
        print(f"The USD estimate is unavailable: {exc}", file=sys.stderr)
        return None
    usd = add_panel_usd(args, tier_estimate_usd(by_role, n_tokens, models, tier, context_share),
                        by_role, n_tokens, tier, context_share, models)
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


ROLES = ("extractor", "answerer", "judge")


def role_usd(args: argparse.Namespace, n_tokens: int, by_role: dict[str, int], tier: str | None,
             context_share: float = 0.5) -> dict[str, float] | None:
    """Each role's list-price dollars, panel judges after the first counted under ``judge``.

    ``None`` when a role has no price. Called once :func:`estimate_usd` has priced every role and
    panel judge, so the models resolve and the values sum to its ``expected_usd`` to rounding.
    """
    from dokimi.novelty.tiers import usd_by_role

    split = usd_by_role(by_role, n_tokens, role_models(args), tier, context_share)
    if split is None:
        return None
    for model, provider in judge_refs(args)[1:]:
        extra = usd_by_role({"judge": by_role.get("judge", 0)}, n_tokens,
                            {"judge": (model, provider)}, tier, context_share) or {}
        split["judge"] = split.get("judge", 0.0) + extra.get("judge", 0.0)
    return {role: round(split.get(role, 0.0), 6) for role in ROLES}


def least_sure_role(usd_by_role: dict[str, float]) -> dict[str, str]:
    """The role the dollar figure is least sure of, and why (#631, #1217).

    The role with the largest dollars at every tier; a tie goes to the first of extractor,
    answerer, judge. Priced at its own ``--dry-run`` call counts with the refit profile, the 17
    paid runs of 2026-11-28 had the largest dollar error in the role with the largest estimated
    dollars in 15. The other two are triage runs where every role's error was under $0.01. At
    standard, study and untiered the role furthest off was the extractor, mostly through its call
    count.
    """
    role = max(ROLES, key=lambda name: usd_by_role[name])
    return {"role": role,
            "reason": f"the {role} has the most dollars, so an error in its call count or its "
                      "token profile moves the figure most"}


def add_role_usd(args: argparse.Namespace, estimate: dict, n_tokens: int, tier: str | None,
                 context_share: float) -> None:
    """Put ``usd_by_role`` and ``least_sure_role`` in a run estimate, both ``None`` without a figure."""
    split = None
    if estimate["expected_usd"] is not None:
        split = role_usd(args, n_tokens, estimate["estimate"]["by_role"], tier, context_share)
    estimate["usd_by_role"] = split
    estimate["least_sure_role"] = None if split is None else least_sure_role(split)


def least_sure_line(estimate: dict) -> str | None:
    """The stderr line naming the least sure role, its share of ``expected_usd`` and why."""
    least = estimate.get("least_sure_role")
    if not least:
        return None
    split = estimate["usd_by_role"]
    share = 100 * split[least["role"]] / (sum(split.values()) or 1)
    return f"Least sure of: {least['role']} ({share:.0f}% of expected_usd): {least['reason']}"


def add_panel_calls(args: argparse.Namespace, estimate: dict) -> None:
    """Count the panel's grading calls in a run estimate: each judge after the first grades every answer again."""

    try:
        extra_judges = max(0, len(judge_refs(args)) - 1)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    extra = extra_judges * estimate["by_role"]["judge"]
    if not extra:
        return
    estimate["by_role"]["judge"] += extra
    for key in ("probe_calls", "total_calls", "total_calls_if_every_pair_escalates",
                "expected_calls"):
        estimate[key] += extra


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
