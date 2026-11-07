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
            raise SystemExit(str(exc)) from None

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
                             "answering model, unless --allow-self-judge")
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
    check_judge2(client, answerer, args.judge2_frac, args.allow_self_judge)
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
