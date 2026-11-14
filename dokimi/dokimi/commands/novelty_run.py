# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run-wide pieces of `novelty analyze` and `novelty probe`: the `--max-usd` flag and the clean
stop at the cap (the cap object is `dokimi.novelty.cost.spend_cap`), the second judge, and the
provenance stamp on the written JSON."""

from __future__ import annotations

import argparse
import functools
import math
import sys
from pathlib import Path


def stops_at_cap(command):
    """Turn a reached spend cap into a clean exit instead of a traceback."""
    from dokimi.novelty.client import SpendCapReached

    @functools.wraps(command)
    def run(args: argparse.Namespace) -> None:
        try:
            command(args)
        except SpendCapReached as exc:
            # A command that keeps its partial result (atoms, probe, analyze) saves it itself.
            raise SystemExit(f"{exc}; nothing was written") from None

    return run


def exit_if_analyze_partial(result, out) -> None:
    """Save and exit when the cap stopped ``analyze`` (``result.partial``); a finished run returns."""
    from dokimi.commands.novelty_budget import save_analyze_partial

    if result.partial:
        save_analyze_partial(result, out)


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


def judge2_client(args: argparse.Namespace, answerer, judge, make_client, panel=()):
    """The ``--judge2`` client, checked against the answerer and every panel judge before any paid call; ``None`` without it."""
    from dokimi.novelty.judge2 import check_judge2

    if not getattr(args, "judge2", None):
        return None
    client = make_client(args.judge2, args.judge2_provider or judge.provider, "judge2", args)
    check_judge2(client, answerer, args.judge2_frac, args.allow_self_judge, judge, panel)
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


def panel_clients(args: argparse.Namespace, make_client) -> tuple:
    """Clients for the judges after the first ``--judge`` (the first is the judge client)."""
    from dokimi.novelty.cost import judge_refs
    from dokimi.utils import InputError

    try:
        refs = judge_refs(args)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    return tuple(make_client(model, provider, "judge", args) for model, provider in refs[1:])


def attach_panel(payload: dict, result, aggregate: str) -> None:
    """Add the ``judge_panel`` block to a written record and print its warnings."""
    from dokimi.novelty.panel_record import panel_record
    from dokimi.novelty.panel import panel_warnings

    judge_panel = panel_record(result, aggregate)
    if judge_panel:
        payload["judge_panel"] = judge_panel
        for note in panel_warnings(judge_panel):
            print(f"WARNING: {note}", file=sys.stderr)


def save_panel_verdicts(result, out: Path) -> Path | None:
    """Write each judge's verdict on every answer beside ``out``, so a panel run can be audited or
    re-aggregated without grading again; ``None`` for a single-judge run."""
    import json

    runs = [run.as_dict() for run in result.measure_result.runs if run.judge_verdicts]
    if not runs:
        return None
    dest = out.with_name(f"{out.stem}.judge_verdicts.json")
    dest.write_text(json.dumps(runs, indent=2), encoding="utf-8")
    return dest
