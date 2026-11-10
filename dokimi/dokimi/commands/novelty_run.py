# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run-wide pieces of `novelty analyze` and `novelty probe`: the spend cap, the second judge,
and the provenance stamp on the written JSON."""

from __future__ import annotations

import argparse
import functools
import math
import sys


def spend_cap(args: argparse.Namespace):
    """The invocation's one ``--max-usd`` cap, shared by every role client."""
    from dokimi.novelty.client import SpendCap

    limit = getattr(args, "max_usd", None)
    if limit is None:
        return None
    if getattr(args, "_spend_cap", None) is None:
        args._spend_cap = SpendCap(limit_usd=limit)
    return args._spend_cap


def stops_at_cap(command):
    """Turn a reached spend cap into a clean exit instead of a traceback."""
    from dokimi.novelty.client import SpendCapReached

    @functools.wraps(command)
    def run(args: argparse.Namespace) -> None:
        try:
            command(args)
        except SpendCapReached as exc:
            # A command that keeps its partial result catches the cap itself and never gets here.
            raise SystemExit(f"{exc}; nothing was written") from None

    return run


def usd_amount(text: str) -> float:
    """A positive, finite USD amount (``0``, negatives, ``nan`` and ``inf`` would disable the cap)."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive number of USD, got {text!r}")
    return value


def add_spend_cap(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-usd", type=usd_amount, default=None, dest="max_usd",
                        help="Stop the run once its model calls have cost this much (USD, "
                             "all roles together; calls in flight can overshoot it). A call "
                             "with no known price also stops it; a reply replayed from "
                             "DOKIMI_RESPONSE_CACHE counts $0, so a stopped run can be resumed "
                             "under the same cap. Default: no cap")


def add_judge2_args(parser: argparse.ArgumentParser, providers) -> None:
    from dokimi.novelty.judge2 import DEFAULT_FRAC

    parser.add_argument("--judge2", default=None, dest="judge2", metavar="MODEL",
                        help="Regrade a seeded subset of the graded runs with this second judge "
                             "and report lenient agreement, kappa on lenient and exact verdict "
                             "agreement (`judge2` in the output). Refused when it is the "
                             "answering model or the first judge, unless --allow-self-judge")
    parser.add_argument("--judge2-frac", type=float, default=DEFAULT_FRAC, dest="judge2_frac",
                        help=f"Share of the graded runs the second judge regrades "
                             f"(default: {DEFAULT_FRAC})")
    parser.add_argument("--judge2-provider", default=None, dest="judge2_provider",
                        choices=providers,
                        help="Provider for --judge2 (default: the judge's provider)")


def judge2_client(args: argparse.Namespace, answerer, judge, make_client):
    """The ``--judge2`` client, checked against the answerer before any paid call; ``None`` without it."""
    from dokimi.novelty.judge2 import check_judge2

    if not getattr(args, "judge2", None):
        return None
    client = make_client(args.judge2, args.judge2_provider or judge.provider, "judge2", args)
    check_judge2(client, answerer, args.judge2_frac, args.allow_self_judge, judge)
    return client


def stamp(payload: dict, args: argparse.Namespace, measure_result, *, answerer, judge,
          judge2=None) -> None:
    """Add ``provenance`` (and ``judge2`` when it ran) to a novelty JSON, and say a same-vendor judge on stderr.

    ``novelty analyze`` already has ``provenance``; only the second judge is added there.
    """
    from dokimi.novelty.judge2 import regrade
    from dokimi.novelty.provenance import provenance, same_vendor_warning

    if "provenance" not in payload:
        payload["provenance"] = provenance(
            artifact_digest=payload.get("ref_digest"), answerer_model=answerer.model,
            judge_model=judge.model, context_framing=args.context_framing)
        warning = same_vendor_warning(payload["provenance"])
        if warning:
            print(f"WARNING {warning}", file=sys.stderr)
    if judge2 is not None:
        from dokimi.novelty.client import SpendCapReached
        try:
            payload["judge2"] = regrade(measure_result, judge2, frac=args.judge2_frac,
                                        seed=args.seed, concurrency=args.concurrency)
        except SpendCapReached as exc:  # keep the paid-for result; only the regrade stops
            payload["judge2"] = {"model": judge2.model, "stopped": str(exc)}
            print(f"WARNING second judge stopped: {exc}", file=sys.stderr)
        payload["judge2"]["cost"] = judge2.cost_summary()


def add_panel_args(parser: argparse.ArgumentParser) -> None:
    """``--judge`` (repeated, it is a panel) and ``--judge-aggregate``."""
    parser.add_argument("--judge", action="append", default=None, dest="judges",
                        metavar="PROVIDER/MODEL",
                        help="A judge, as PROVIDER/MODEL; repeated, it is a panel: every judge "
                             "grades every answer and --judge-aggregate decides. Pick judges "
                             "from different vendors (docs/judge-panel-study.md: three from "
                             "three families cut the judge spread by a third). Replaces "
                             "--judge-model/--judge-provider; the first judge is the default "
                             "extractor")
    parser.add_argument("--judge-aggregate", default="median", dest="judge_aggregate",
                        choices=("median", "majority", "unanimous"),
                        help="How a panel's verdicts become one: median of the verdict scale "
                             "(default), lenient majority, or unanimous (conservative; moved "
                             "the study's headline by up to 0.20)")


def judge_refs(args: argparse.Namespace) -> list[tuple[str, str]]:
    """The ``--judge PROVIDER/MODEL`` list as (model, provider) pairs; empty without the flag."""
    from dokimi.llm import ANSWER_PROVIDERS as PROVIDERS

    refs = getattr(args, "judges", None) or []
    if refs and (getattr(args, "judge_model", None) or getattr(args, "judge_provider", None)):
        raise ValueError("--judge replaces --judge-model and --judge-provider; pass one form")
    out: list[tuple[str, str]] = []
    for ref in refs:
        provider, sep, model = ref.partition("/")
        if not sep or not model or provider not in PROVIDERS:
            raise ValueError(f"--judge {ref!r} is not PROVIDER/MODEL with PROVIDER one of "
                             f"{', '.join(PROVIDERS)}")
        if any(m == model for m, _ in out):
            # the panel keys each judge's verdicts by model name
            raise ValueError(f"--judge {ref!r}: model {model!r} is given twice")
        out.append((model, provider))
    return out


def panel_clients(args: argparse.Namespace, make_client) -> tuple:
    """Clients for the judges after the first ``--judge`` (the first is the judge client)."""
    from dokimi.utils import InputError

    try:
        refs = judge_refs(args)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    return tuple(make_client(model, provider, "judge", args) for model, provider in refs[1:])


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


def attach_panel(payload: dict, result, aggregate: str) -> None:
    """Add the ``judge_panel`` block to a written record and print its warnings."""
    from dokimi.novelty.measure import panel_record
    from dokimi.novelty.panel import panel_warnings

    judge_panel = panel_record(result, aggregate)
    if judge_panel:
        payload["judge_panel"] = judge_panel
        for note in panel_warnings(judge_panel):
            print(f"WARNING: {note}", file=sys.stderr)


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
