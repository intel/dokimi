# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""`skill routing` and `catalog routing`: can a skill be selected against its competitors?

`dokimi.commands.routing_parser` registers both leaves; the field and the queries are read in
`novelty_routing_inputs`, the result is saved and printed in `novelty_routing_report`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from dokimi.commands.novelty import _default_dir
from dokimi.commands.novelty_routing_inputs import checked_out_path, load_all_vs_all_field, load_routing_field
from dokimi.commands.novelty_routing_report import (
    check_all_vs_all_args,
    check_all_vs_all_field,
    drop_queries_left_out,
    exit_partial_all_vs_all,
    field_record,
    no_competitors_conflict,
    no_queries_reason,
    print_all_vs_all_estimate,
    print_all_vs_all_summary,
    print_routing_estimate,
    print_routing_summary,
    refuse_estimate_without_queries,
    routing_queries,
    save_result,
    stop_if_partial,
    warn_blind_skills,
)
from dokimi.utils import InputError


def _cmd_routing_all_vs_all(args: argparse.Namespace) -> None:
    """Every skill in the field is the gold in turn: confusion matrix and attractors."""
    from dokimi.novelty import cost
    from dokimi.novelty.all_vs_all import all_vs_all, check_acceptable, load_queries_with_acceptable
    from dokimi.novelty.client import roll_up_cost
    from dokimi.novelty.routing import tokenize

    check_all_vs_all_args(args)
    left_out: list[str] = []
    dropped: dict[str, set[str]] = {}
    conflicts: dict[str, list[str]] = {}
    unloaded: dict[str, str] = {}
    field = load_all_vs_all_field(args, left_out, dropped, conflicts, unloaded)
    try:
        queries_by_skill, acceptable = load_queries_with_acceptable(args.queries_by_skill)
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read the queries: {exc}") from exc
    check_all_vs_all_field(args, field, dropped, conflicts)
    queries_by_skill, dropped_queries = drop_queries_left_out(queries_by_skill, field, dropped,
                                                              unloaded)
    check_acceptable([c.name for c in field], queries_by_skill, acceptable)
    if args.estimate_only:
        print_all_vs_all_estimate(args, field, queries_by_skill)
        return
    selector = cost.client(args.model, args.provider, "selector", args) if args.model else None
    if selector is not None:
        # One call per query with a content word and per repeat, each sending every card.
        calls = sum(bool(tokenize(q)) for qs in queries_by_skill.values() for q in qs)
        calls *= args.repeats
        print(
            f"all-vs-all: --model makes {calls} model calls, one per query and repeat, over "
            f"{len(field)} cards each",
            file=sys.stderr,
        )
    result = all_vs_all(field, queries_by_skill, client=selector, seed=args.seed,
                        repeats=args.repeats, acceptable=acceptable)
    without = [c.name for c in field if c.name not in queries_by_skill]
    if without:
        print(f"WARNING: {len(without)} skill(s) in the field have no queries, so they only "
              f"compete: {', '.join(sorted(without))}", file=sys.stderr)
    warn_blind_skills(result, queries_by_skill)

    model_arm = result.get("model") or {}
    failed = model_arm.get("n_failed", 0)
    sent = sum(model_arm.get("n_scored", {}).values()) + failed  # contentless ones are not sent
    if failed:
        print(f"WARNING: {failed} model call(s) failed (first error: "
              f"{model_arm['first_error']}); failed calls are left out of model-arm scoring, "
              "and a query with a failed repeat out of the repeat-noise rates", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mode": "all_vs_all", "queries_by_skill": args.queries_by_skill,
               "n_candidates": len(field), "result": result}
    payload["field"] = field_record(args, field, left_out, dropped_queries)
    if selector is not None:
        payload["cost"] = roll_up_cost([selector])
    out = save_result(payload, out, model_arm)
    summary = (result, len(field), len(queries_by_skill), model_arm, payload.get("cost"))
    stop_if_partial(payload, out, lambda: print_all_vs_all_summary(*summary))
    if failed:
        exit_partial_all_vs_all(model_arm, payload.get("cost"), failed, sent, out)
    print(f"OK Saved all-vs-all routing: {out}")
    print_all_vs_all_summary(*summary)


def cmd_routing(args: argparse.Namespace) -> tuple[dict, Path, str] | None:
    """Can a skill (or each skill under a root) be selected against its competitors? Lexical floor, model on top."""
    from dokimi.novelty import cost
    if args.repeats > 1 and not args.model:
        raise InputError("--reps needs --model: the lexical floor is deterministic, so repeating it measures nothing")
    from dokimi.novelty.routing import (
        check_field,
        lexical_routing,
        model_routing,
    )

    if args.all_vs_all:
        _cmd_routing_all_vs_all(args)
        return None
    gold, body, competitors, left_out, conflicts = load_routing_field(args)
    # Checked before the query file is read, so the message is about the real problem.
    if not competitors and conflicts:
        raise InputError(no_competitors_conflict(args, conflicts))
    check_field(gold, competitors)

    queries, self_derived, queries_source = routing_queries(args, body)
    candidates = [gold, *competitors]
    if args.estimate_only:
        refuse_estimate_without_queries(args, queries, self_derived)
        print_routing_estimate(args, candidates, queries)
        return None
    lexical = dict(lexical_routing(candidates, queries, gold=0),
                   queries_self_derived=self_derived)
    out = checked_out_path(args.out, "routing", default=str(_default_dir(args.artifact) / "routing.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not lexical["n_queries_scored"]:
        _refuse_no_queries(args, queries, self_derived, out, len(candidates), queries_source)

    payload = {
        "artifact": args.artifact,
        "status": "ok",
        "n_candidates": len(candidates),
        "queries_self_derived": self_derived,
        "queries_source": queries_source,
        "field": field_record(args, competitors, left_out, gold=gold),
        "lexical": lexical,
    }
    if args.model:
        from dokimi.novelty.client import roll_up_cost

        selector = cost.client(args.model, args.provider, "selector", args)
        payload["model"] = dict(
            model_routing(candidates, queries, client=selector, gold=0, seed=args.seed,
                          repeats=args.repeats),
            queries_self_derived=self_derived,
        )
        payload["cost"] = roll_up_cost([selector])

    out = save_result(payload, out, payload.get("model", {}))
    try:
        stop_if_partial(payload, out,
                        lambda: print_routing_summary(payload, len(competitors), self_derived))
    except SystemExit as stop:
        raise RoutingStopped(stop.code, payload, out, gold.name) from None
    print(f"OK Saved routing measurement: {out}")

    print_routing_summary(payload, len(competitors), self_derived)
    return payload, out, gold.name


class RoutingStopped(SystemExit):
    """A routing run the cap or a model error cut short: it exits as before, and carries the saved result for --json."""

    def __init__(self, message: object, payload: dict, out: Path, skill: str) -> None:
        super().__init__(message)
        self.result = (payload, out, skill)


def cmd_skill_routing(args: argparse.Namespace) -> None:
    """`skill routing`, and with --json its cli_output.v1 document. Report only: the leaf issues no verdict.

    The lexical floor from self-derived (heading) queries is triage, not a routing rate, so
    ``summary.queries_self_derived`` is carried next to every score.
    """
    if not getattr(args, "json", False):
        cmd_routing(args)
        return
    from dokimi import cli_output

    stopped = None
    with cli_output.quiet_stdout():
        try:
            done = cmd_routing(args)
        except RoutingStopped as stop:  # the result is saved: its document is printed, then the exit stands
            done, stopped = stop.result, stop
    if done is None:  # --dry-run: an estimate, nothing measured
        cli_output.emit_leaf("skill routing", None, reason="estimate only, nothing measured")
        return
    payload, out, skill = done
    lexical, model = payload["lexical"], payload.get("model") or {}
    spent = (payload.get("cost") or {}).get("usd")
    calls = (payload.get("cost") or {}).get("llm_calls", 0)
    cost = {"spent_usd": spent or 0.0, "cap_usd": getattr(args, "max_usd", None), "calls": calls,
            "priced": None if not calls else spent is not None}
    if cost["cap_usd"] is not None and cost["priced"] is not False:  # an unpriced spend leaves no remainder to state
        cost["remaining_usd"] = round(cost["cap_usd"] - cost["spent_usd"], 6)
    partial = payload.get("partial")
    status = "complete" if partial is None else ("budget_stopped" if partial["reason"] == "spend_cap" else "partial")
    cli_output.emit_leaf(
        "skill routing", payload, status=status, reason=partial and partial["message"],
        subjects=[{"subject": skill, "verdict": None}],
        summary={"queries_self_derived": payload["queries_self_derived"], "n_candidates": payload["n_candidates"],
                 "lexical_recall_at_1": lexical.get("recall_at_1"), "model_selection_rate": model.get("selection_rate")},
        artifacts={"out": str(out)}, cost=cost)
    if stopped is not None:
        raise stopped


def _refuse_no_queries(args: argparse.Namespace, queries: list[str], self_derived: bool,
                       out: Path, n_candidates: int,
                       queries_source: str | None = None) -> NoReturn:
    """Write the ``no_queries`` record, then exit saying why no query was scored."""
    # Written too, so a batch run leaves a record of why it has no rate.
    reason = no_queries_reason(args, queries, self_derived)
    out.write_text(json.dumps({"artifact": args.artifact, "status": "no_queries",
                               "reason": reason, "queries_self_derived": self_derived,
                               "queries_source": queries_source or ("none" if self_derived else None),
                               "n_candidates": n_candidates}, indent=2),
                   encoding="utf-8")
    raise InputError(f"no usable queries: {reason}. Pass --queries <file> with requests written independently "
               f"of the artifact, one per line or a JSON list. Wrote {out} with status no_queries")
