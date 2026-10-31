# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Queries, console lines and refusals of ``novelty routing``.

Split from ``commands/novelty.py``, which keeps the command bodies, so that file stays under
its growth cap. Nothing here builds a client; a model arm is always the caller's.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from dokimi.exit_codes import usage_exit

if TYPE_CHECKING:
    from dokimi.novelty.routing import Candidate


def routing_queries(args: argparse.Namespace, body: str,
                    gold: Candidate) -> tuple[list[str], bool, str | None]:
    """The queries of one routing run: ``(queries, self_derived, queries_source)``.

    ``--queries`` is read as written. ``--queries-from-headings`` derives them from the artifact
    itself and names the source: ``headings``, ``description``, or ``none`` when neither gives any.
    """
    from dokimi.novelty.routing import RoutingInputError, load_queries, self_derived_queries

    if args.queries:
        try:
            return load_queries(args.queries), False, None
        except RoutingInputError as exc:
            usage_exit(str(exc))
    if not args.queries_from_headings:
        usage_exit("pass --queries <file> with requests written independently of the artifact, or "
                   "--queries-from-headings for a self-derived triage run")
    queries, source = self_derived_queries(body, gold.description)
    if source == "none":
        # Nothing was derived, so there is nothing to label; the refusal that follows says why.
        return queries, True, source
    print(f"WARNING: queries derived from the artifact's own "
          f"{'headings' if source == 'headings' else 'description'}. The artifact "
          "then defines both the problem scope and its own relevance, so this is triage, "
          "not a routing rate", file=sys.stderr)
    return queries, True, source


def estimate_routing_usd(model: str, provider: str, prompts: list[int],
                         repeats: int) -> float | None:
    """The list-price USD of the calls of a routing run, priced one call at a time.

    A long-context tier applies to the prompt of one call, so the run's total prompt is not
    priced as one completion: each query's prompt and one pick are priced on their own, then
    summed and multiplied by the repeats. ``None`` when a call has no list price.
    """
    from dokimi.llm_pricing import list_cost_usd
    from dokimi.novelty.routing import PICK_REPLY_TOKENS

    total = 0.0
    for prompt in prompts:
        cost = list_cost_usd(model, provider, prompt, PICK_REPLY_TOKENS)
        if cost is None:
            return None
        total += cost
    return round(total * repeats, 6)


def print_routing_estimate(args: argparse.Namespace, candidates: list, queries: list[str]) -> None:
    """The model arm's calls, tokens and list-price USD, as JSON; nothing is sent.

    No client is built. Without ``--model`` the run is lexical only, so the bill is zero.
    """
    from dokimi.novelty.routing import selection_estimate, selection_prompt_tokens

    # Without --model there is no selector, so no call is made, whatever --repeats says.
    estimate = selection_estimate(candidates, queries,
                                  repeats=args.repeats if args.model else 0)
    usd: float | None = 0.0
    if args.model and estimate["n_calls"]:
        usd = estimate_routing_usd(args.model, args.provider,
                                   selection_prompt_tokens(candidates, queries), args.repeats)
    print(json.dumps({"estimate_only": True, "artifact": args.artifact, "model": args.model,
                      "provider": args.provider if args.model else None,
                      "n_candidates": len(candidates), **estimate,
                      "expected_usd": usd}, indent=2))
    print("Estimate only, nothing was measured. Input is the selection prompt at chars / 4 per "
          "call; output is one JSON pick. A reasoning model bills more output than this.",
          file=sys.stderr)
    if args.model and estimate["n_calls"] and usd is None:
        print(f"expected_usd is null: {args.model} has no list price in the litellm price map",
              file=sys.stderr)


def print_routing_summary(payload: dict, n_competitors: int, self_derived: bool) -> None:
    """The lexical line, the model line and their caveats, printed after a routing run."""
    # Every rate is printed with the number of queries behind it, and a self-derived one
    # carries the triage tag on the same line, so a copied figure cannot lose either.
    tag = "  triage: self-derived" if self_derived else ""
    lexical = payload["lexical"]
    print(f"lexical  R@1 {lexical['recall_at_1']}  R@3 {lexical['recall_at_3']}  "
          f"MRR {lexical['mrr']}  n_queries {lexical['n_queries_scored']} of "
          f"{lexical['n_queries_given']} given, vs {n_competitors} competitors{tag}"
          + ("  (R@3 needs more than 3 candidates to mean anything)"
             if lexical["recall_at_3"] is None else ""))
    if lexical["n_queries_scored"] < 5:
        n_scored = lexical["n_queries_scored"]
        noun = "query" if n_scored == 1 else "queries"
        print(f"WARNING: only {n_scored} {noun} scored; one query moves "
              "a rate by 20 points or more, so do not quote it as a routing rate")
    if "model" in payload:
        row = payload["model"]
        print(f"model    selection {row['selection_rate']}  "
              f"({row['selection_rate_among_answered']} among answered; "
              f"{row['n_abstained']} abstained, {row['n_failed']} failed)  "
              f"[{row['model']}]  n_queries {row['n_queries']} given{tag}")
        if row["n_abstained"]:
            of_calls = f" of {row['n_calls']} calls" if row["repeats"] > 1 else ""
            print(f"{row['n_abstained']}{of_calls} abstained or unparseable: the raw reply "
                  "is under per_query[].raw_reply in the output")
        if row["noise"]:
            print_noise(row)
        print("The model figure is an upper bound on harness behaviour: a harness that "
              "truncates descriptions in its own listing selects on less text.")
        print_cost(payload["cost"])
    if self_derived:
        print("Self-derived queries: read as triage only.")


def print_noise(model_arm: dict) -> None:
    """The repeat noise of a model arm, as its one console line."""
    noise = model_arm["noise"]
    print(f"noise    {model_arm['repeats']} repeats per query: mean agreement "
          f"{noise['mean_agreement']}, {noise['unstable_queries']} queries picked "
          f"differently across repeats; selection by repeat "
          f"{noise['selection_rate_by_repeat']}, half range "
          f"{noise['half_range']} (a crude sampling floor)")


def print_cost(cost: dict) -> None:
    """The usage block of a ``--model`` run, as its one console line."""
    if not cost["llm_calls"]:
        print("cost     0 calls")
        return
    spent = ("USD not known: a call had no price or no usage" if cost["usd"] is None
             else "<$0.0001" if 0 < cost["usd"] < 0.00005 else f"${cost['usd']:.4f}")
    replayed = (f", {cost['replayed_calls']} replayed from the cache and not counted"
                if cost["replayed_calls"] else "")
    print(f"cost     {cost['llm_calls']} calls, {cost['input_tokens']} input / "
          f"{cost['output_tokens']} output tokens, cache read "
          f"{cost['cache_read_tokens']} / write {cost['cache_write_tokens']}, "
          f"{spent}{replayed}")


def print_attractors(result: dict) -> None:
    """The top five attractors of each arm, one line each, and what the arm left out.

    A tie and an unscored query name no pick, so a "no skill was picked" line beside them
    would read as a clean result; the line after the attractors says how many there were.
    Skills with the same description words are one tied group, named once, not each other's
    attractor.
    """
    from dokimi.novelty.all_vs_all import TIE

    for group in result["tied_groups"]:
        print(f"tied: {', '.join(group)} have the same description; one group, not attractors "
              "of each other")
    for arm in ("lexical", "model"):
        if arm not in result:
            continue
        section = result[arm]
        # The model arm counts calls, one per query and repeat; the lexical arm counts queries.
        unit = "calls" if section.get("repeats", 1) > 1 else "queries"
        rows = section["attractors"][:5]
        if not rows:
            print(f"{arm}: no skill was picked for another skill's query")
        for row in rows:
            print(f"{arm}: {row['skill']} picked for {row['wrongly_picked']} of "
                  f"{row['sibling_queries']} sibling {unit} (from {', '.join(row['from'])})")
        tied = sum(picks.get(TIE, 0) for picks in section["confusion"].values())
        unscored = sum(section["n_unscored"].values())
        if tied or unscored:
            print(f"{arm}: {tied} tied and {unscored} unscored {unit} are not counted as picks")


def print_all_vs_all_model(model_arm: dict, cost: dict | None) -> None:
    """The noise and cost lines of an all-vs-all run; silent without ``--model``."""
    if model_arm.get("noise"):
        print_noise(model_arm)
    if cost is not None:
        print_cost(cost)
