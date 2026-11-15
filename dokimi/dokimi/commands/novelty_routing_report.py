# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Queries, console lines and refusals of ``skill routing`` and ``catalog routing``.

Split from ``commands/novelty.py``, which keeps the command bodies, so that file stays under
its growth cap. Nothing here builds a client; a model arm is always the caller's.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from dokimi.utils import InputError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from dokimi.novelty.routing import Candidate


def routing_queries(args: argparse.Namespace, body: str) -> tuple[list[str], bool, str | None]:
    """The queries of one routing run: ``(queries, self_derived, queries_source)``.

    ``--queries`` is read as written. ``--queries-from-headings`` derives them from the artifact's
    own headings; ``queries_source`` is ``headings``, or ``none`` when no heading gives any.
    """
    from dokimi.novelty.routing import RoutingInputError, headings_as_queries, load_queries

    if args.queries:
        try:
            return load_queries(args.queries), False, None
        except RoutingInputError as exc:
            raise InputError(str(exc)) from exc
    if not args.queries_from_headings:
        raise InputError("pass --queries <file> with requests written independently of the artifact, or "
                   "--queries-from-headings for a self-derived triage run")
    queries = headings_as_queries(body)
    if not queries:
        # Nothing was derived, so there is nothing to label; the refusal that follows says why.
        return queries, True, "none"
    print("WARNING: queries derived from the artifact's own headings. The artifact "
          "then defines both the problem scope and its own relevance, so this is triage, "
          "not a routing rate", file=sys.stderr)
    return queries, True, "headings"


def estimate_routing_usd(model: str, provider: str, prompts: list[int],
                         repeats: int) -> float | None:
    """The list-price USD of the calls of a routing run, priced one call at a time.

    A long-context tier applies to the prompt of one call, so the run's total prompt is not
    priced as one completion: each query's prompt and ``SELECTION_REPLY_TOKENS`` of reply are
    priced on their own, then summed and multiplied by the repeats. ``None`` when a call has no list price.
    """
    from dokimi.llm_pricing import list_cost_usd
    from dokimi.novelty.routing_estimate import SELECTION_REPLY_TOKENS

    total = 0.0
    for prompt in prompts:
        cost = list_cost_usd(model, provider, prompt, SELECTION_REPLY_TOKENS)
        if cost is None:
            return None
        total += cost
    return round(total * repeats, 6)


def no_queries_reason(args: argparse.Namespace, queries: list[str], self_derived: bool) -> str:
    """Why a routing run has no query to score."""
    if not queries and self_derived:
        return (f"no section heading of two or more words outside code fences in "
                f"{args.artifact}; headings inside fenced code are not requests")
    if not queries:
        return f"--queries {args.queries} holds no non-blank query"
    return (f"none of the {len(queries)} queries has a content word, since words "
            "like a, the, how do not count")


def refuse_estimate_without_queries(args: argparse.Namespace, queries: list[str],
                                    self_derived: bool) -> None:
    """Exit as the real run would when no query has a content word, so no budget is read off it."""
    from dokimi.novelty.routing import tokenize

    if not any(tokenize(query) for query in queries):
        raise InputError(f"no usable queries: {no_queries_reason(args, queries, self_derived)}. "
                   "Nothing is estimated, since the run would be refused")


# Why a field rule left a skill out: the phrase for the reason, the flag that keeps the skill, and
# the words that name that flag, in the order they are named.
_LEFT_OUT = {
    "disabled": ("sets disable-model-invocation", "--include-disabled",
                 "--include-disabled keeps it"),
    "invalid": ("has a spec error", "--include-invalid", "--include-invalid keeps it"),
    "conflict": ("has copies with different content", "--keep-copies-under",
                 "--keep-copies-under <dir> keeps only the copies under <dir>"),
}


def conflict_hint(catalog: str, conflicts: dict[str, list[str]], all_vs_all: bool = False) -> str:
    """Where the copies of each conflicting name sit, and the ways out of each conflict.

    A skill directory under another directory of the catalog (``skills/review``) is a
    ``--against`` to point at, and a ``--keep-copies-under`` to keep. A skill directory directly
    beneath the catalog (``review``) is only a ``--keep-copies-under``: its name is the directory
    to keep. A ``SKILL.md`` in the catalog root is neither: its copy routes only once it is
    deduped or renamed. A directory is suggested only when it holds exactly one of the name's
    copies, since one that holds two (``skills`` over ``skills/legacy/review``) keeps the
    conflict. The ways out are given per name: ``--keep-copies-under`` takes one directory.
    """
    texts = [_conflict_hint_for(catalog, name, paths, all_vs_all)
             for name, paths in sorted(conflicts.items())]
    if len(texts) > 1:
        texts.append("--keep-copies-under takes one directory, so one run resolves only the names "
                     "whose copies that directory splits.")
    return " ".join(texts)


def _conflict_hint_for(catalog: str, name: str, paths: list[str], all_vs_all: bool = False) -> str:
    """The ways out of one name's conflict: the directories that hold exactly one of its copies."""
    from dokimi.novelty.catalog_keep import under

    rels = [Path(os.path.relpath(os.path.abspath(p), os.path.abspath(catalog))).parts for p in paths]
    places = {_copy_place(path, catalog) for path in paths}

    def holds_one(directory: str) -> bool:
        parts = Path(directory).parts
        return sum(rel[:len(parts)] == parts for rel in rels) == 1

    def keeps_one(directory: str) -> bool:  # the keep rule: a symlinked copy sits where it points
        prefix = (Path(catalog) / directory).resolve()
        return sum(under(Path(p), prefix) for p in paths) == 1

    holders = sorted({d for kind, d in places if kind == "holder" and holds_one(d)})
    skills = sorted({d for kind, d in places if kind == "skill" and holds_one(d)})
    at_root = any(kind == "root" for kind, _ in places)
    kept = [d for d in holders + skills if keeps_one(d)]
    if not holders and not kept:
        picks_flag = "no --keep-copies-under" if all_vs_all else "no --against or --keep-copies-under"
        return (f"`{name}`: {picks_flag} picks one of its copies (they sit "
                "at the catalog root or one inside another's directory): dedupe or rename them.")
    keeps = " or ".join(f"--keep-copies-under {d}" for d in kept)
    where = [f"under {d}" for d in holders] + [f"in the skill directory {d}" for d in skills]
    if at_root:
        where.append("at the catalog root")
    if holders and all_vs_all:
        picks = " or ".join(f"catalog routing {os.path.join(catalog, d)}" for d in holders)
        text = (f"`{name}`: copies sit {' and '.join(where)}: run one of those directories as its "
                f"own catalog ({picks})"
                + (f", or keep only one directory's copies with {keeps}." if kept else "."))
    elif holders:
        picks = " or ".join(f"--against {os.path.join(catalog, d)}" for d in holders)
        text = (f"`{name}`: copies sit {' and '.join(where)}: point --against at one of those "
                f"directories ({picks})"
                + (f", or keep only one directory's copies with {keeps}." if kept else "."))
    else:
        text = f"`{name}`: copies sit {' and '.join(where)}: keep only one with {keeps}."
    if at_root:
        text += " The copy at the catalog root routes only once it is deduped or renamed."
    return text


def _copy_place(path: str, catalog: str) -> tuple[str, str]:
    """Where a copy of a skill sits under the catalog, as ``(kind, directory)``.

    ``root``: a ``SKILL.md`` directly in the catalog (the directory is ``.``, which no flag picks).
    ``skill``: a skill directory directly beneath the catalog, e.g. ``review/SKILL.md``; the
    directory is the skill's own, and ``--keep-copies-under`` keeps it. ``holder``: a skill
    directory under another directory, e.g. ``skills/review/SKILL.md``; the directory holds it,
    and both ``--against`` and ``--keep-copies-under`` take it.
    """
    rel = Path(os.path.relpath(os.path.abspath(path), os.path.abspath(catalog)))
    if len(rel.parts) <= 1:
        return "root", "."
    if len(rel.parts) == 2:
        return "skill", rel.parent.as_posix()
    return "holder", rel.parent.parent.as_posix()


def no_competitors_conflict(args: argparse.Namespace, conflicts: dict[str, list[str]]) -> str:
    """The refusal for a single-artifact run whose field the name conflicts left empty."""
    names = ", ".join(f"`{n}`" for n in sorted(conflicts))
    return ("routing needs competitors, and the catalog left none in the field: " + names
            + " have copies with different content, so the field leaves them out. "
            + conflict_hint(args.catalog, conflicts, bool(getattr(args, "all_vs_all", False)))
            + " Selecting from one candidate measures whether the model will use a tool at "
            "all, not whether this artifact wins")


def check_all_vs_all_args(args: argparse.Namespace) -> None:
    """Refuse the flag combinations ``catalog routing`` cannot run with, and name every missing flag."""
    if args.queries or args.queries_from_headings:
        raise InputError("catalog routing takes --queries-by-skill, not --queries or --queries-from-headings")
    # --out is not needed to estimate, which writes nothing, as a single-artifact estimate.
    missing = {}
    if not args.queries_by_skill:
        missing["--queries-by-skill <file>"] = ("a JSON object of skill name to the requests "
                                                "that should select it")
    if not args.out and not args.estimate_only:
        missing["--out <file>"] = "where the result is written"
    if missing:
        raise InputError(f"catalog routing needs {' and '.join(missing)}: "
                         + "; ".join(f"{flag} is {what}" for flag, what in missing.items()))
    if args.out and not args.estimate_only:
        from dokimi.commands.novelty_routing_inputs import checked_out_path

        checked_out_path(args.out, "catalog routing")


def check_all_vs_all_field(args: argparse.Namespace, field: list, dropped: dict[str, set[str]],
                           conflicts: dict[str, list[str]]) -> None:
    """Refuse a field of fewer than two skills, naming each skill the field left out and why.

    A field that names a skill twice is refused too, before the estimate prices it, as the
    measured run refuses it (:func:`check_field_names`).
    """
    from dokimi.novelty.all_vs_all import check_field_names

    check_field_names([c.name for c in field])
    if len(field) >= 2:
        return
    left = _left_out_list(_left_out_only(dropped, field))
    hint = f" {conflict_hint(args.catalog, conflicts, bool(getattr(args, 'all_vs_all', False)))}" if conflicts else ""
    raise InputError("catalog routing needs at least two skills in the field"
                     + (f": the field leaves out {left}" if left else "") + "." + hint)


def _left_out_reasons(name: str, dropped: dict[str, set[str]]) -> tuple[str, str]:
    """Why the field leaves ``name`` out, and the flags that keep it, as two phrases."""
    reasons = [_LEFT_OUT[why] for why in _LEFT_OUT if why in dropped.get(name, ())]
    return " and ".join(why for why, _, _ in reasons), "; ".join(keep for _, _, keep in reasons)


def _left_out_only(dropped: dict[str, set[str]], field: list[Candidate]) -> dict[str, set[str]]:
    """``dropped`` without the names the field still has: a skill with one copy left in is in it."""
    names = {c.name for c in field}
    return {name: why for name, why in dropped.items() if name not in names}


def _left_out_list(dropped: dict[str, set[str]]) -> str:
    """Each skill the field left out, with its reason and the flag that keeps it, in one line."""
    return "; ".join(f"`{name}` {why}; {keep}" for name in sorted(dropped)
                     for why, keep in [_left_out_reasons(name, dropped)])


def _unexplained_message(names: list[str], unloaded: dict[str, str]) -> str:
    """The refusal for queries naming skills no field rule explains.

    A catalog skill that failed to load is named with why it did not load, since it is in the
    catalog. Any other name is more likely a misspelling.
    """
    sentences = [f"`{n}` is a catalog skill that failed to load: {unloaded[n]}"
                 for n in names if n in unloaded]
    missing = [n for n in names if n not in unloaded]
    if missing:
        which = "that name" if missing == names and len(missing) == 1 else (
            ", ".join(f"`{n}`" for n in missing))
        sentences.append(f"No catalog skill or --competitors has {which}; check the spelling")
    return ("the queries name skills that are not in the field: "
            + ", ".join(f"`{n}`" for n in names) + ". " + ". ".join(sentences))


def drop_queries_left_out(queries_by_skill: dict[str, list[str]], field: list[Candidate],
                          dropped: dict[str, set[str]], unloaded: dict[str, str],
                          ) -> tuple[dict[str, list[str]], list[dict]]:
    """The queries of the skills the field left out dropped, each with a WARNING naming its flag.

    A name the field left out for a rule (``dropped``) loses its queries; the run goes on with the
    rest. A name with a copy that failed to load is refused, even when another copy is in the
    field. A name no rule explains is refused: a catalog skill that failed to load says why, and any
    other name is more likely a misspelling. Refused too when no query is left to route. Returns the
    kept queries and one record per dropped skill, for the saved field record:
    ``{"skill", "reasons", "keep_flags", "queries"}``, so ``queries`` sums to the dropped count.
    Warns on stderr of the kept queries several skills share, before any estimate or model call.
    """
    names = {c.name for c in field}
    # a copy that failed to load may differ from the one that loaded, so the name is not measured
    half_loaded = sorted(n for n in queries_by_skill if n in names and n in unloaded)
    if half_loaded:
        raise InputError("the queries name skills with a catalog copy that failed to load beside "
                         "one that loaded, so which copy they measure is unknown: "
                         + "; ".join(f"`{n}`: {unloaded[n]}" for n in half_loaded)
                         + ". Fix or remove the broken copy")
    # a name with a copy that failed to load is refused even when a rule also left a copy out
    unexplained = sorted(n for n in queries_by_skill
                         if n not in names and (n in unloaded or n not in dropped))
    if unexplained:
        raise InputError(_unexplained_message(unexplained, unloaded))
    kept = {name: queries for name, queries in queries_by_skill.items() if name in names}
    records: list[dict] = []
    for name in sorted(set(queries_by_skill) - names):
        why, keep = _left_out_reasons(name, dropped)
        count = len(queries_by_skill[name])
        print(f"WARNING: dropped {count} {'query' if count == 1 else 'queries'} of `{name}`: the "
              f"field leaves it out, since it {why}; {keep}", file=sys.stderr)
        reasons = [reason for reason in _LEFT_OUT if reason in dropped.get(name, ())]
        records.append({"skill": name, "reasons": reasons,
                        "keep_flags": [_LEFT_OUT[reason][1] for reason in reasons],
                        "queries": count})
    if not kept:
        queried = {name: why for name, why in dropped.items() if name in queries_by_skill}
        why = ("" if not queries_by_skill else ": the queries name only skills the field leaves "
               f"out ({_left_out_list(_left_out_only(queried, field))})")
        raise InputError(f"no skill in the field has queries to route{why}")
    warn_shared_queries(kept)
    return kept, records


def _print_estimate(args: argparse.Namespace, candidates: list, sent: list[str],
                    estimate: dict) -> None:
    """The estimate as JSON, with the list-price USD of the calls ``sent`` makes; nothing is sent."""
    from dokimi.novelty.routing_estimate import (
        SELECTION_CHARS_PER_TOKEN,
        SELECTION_REPLY_TOKENS,
        selection_prompt_tokens,
    )

    usd: float | None = 0.0
    if args.model and estimate["n_calls"]:
        usd = estimate_routing_usd(args.model, args.provider,
                                   selection_prompt_tokens(candidates, sent), args.repeats)
    print(json.dumps({"estimate_only": True, "artifact": args.artifact, "model": args.model,
                      "provider": args.provider if args.model else None,
                      "n_candidates": len(candidates), **estimate,
                      "expected_usd": usd}, indent=2))
    print("Estimate only, nothing was measured. Input is the selection prompt at "
          f"{SELECTION_CHARS_PER_TOKEN} characters per token (chars_per_input_token); output is "
          f"{SELECTION_REPLY_TOKENS} tokens a call (output_tokens_per_call), inside the 114 to 193 "
          "that two models billed. With chars / 4 and 65 the estimate was about 1.55x below the "
          "bills. Another model, a tokenizer or a reasoning model can differ.",
          file=sys.stderr)
    if args.model and estimate["n_calls"] and usd is None:
        print(f"expected_usd is null: {args.model} has no list price in the litellm price map",
              file=sys.stderr)


def print_routing_estimate(args: argparse.Namespace, candidates: list, queries: list[str]) -> None:
    """The model arm's calls, tokens and list-price USD, as JSON; nothing is sent.

    No client is built. Without ``--model`` the run is lexical only, so the bill is zero.
    """
    from dokimi.novelty.routing_estimate import selection_estimate

    # Without --model there is no selector, so no call is made, whatever --reps says.
    estimate = selection_estimate(candidates, queries,
                                  repeats=args.repeats if args.model else 0)
    _print_estimate(args, candidates, queries, estimate)


def print_all_vs_all_estimate(args: argparse.Namespace, field: list[Candidate],
                              queries_by_skill: dict[str, list[str]]) -> None:
    """The all-vs-all run's calls, priced as :func:`print_routing_estimate` prices one artifact's.

    The model arm sends each query with a content word, every skill's queries with the whole field
    on each call, so those are what is counted. ``n_queries`` is every query the run would route.
    """
    from dokimi.novelty.routing import tokenize
    from dokimi.novelty.routing_estimate import selection_estimate

    sent = [query for queries in queries_by_skill.values() for query in queries if tokenize(query)]
    estimate = selection_estimate(field, sent, repeats=args.repeats if args.model else 0)
    estimate["n_queries"] = sum(len(queries) for queries in queries_by_skill.values())
    _print_estimate(args, field, sent, estimate)


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
        partial = payload.get("partial")
        basis = (f"partial: {partial['calls_done']} of {partial['calls_planned']} calls"
                 if partial else f"n_queries {row['n_queries']} given")
        print(f"model    selection {row['selection_rate']}  "
              f"({row['selection_rate_among_answered']} among answered; "
              f"{row['n_abstained']} abstained, {row['n_failed']} failed)  "
              f"[{row['model']}]  {basis}{tag}")
        answered = row["n_calls"] - row["n_failed"]
        if partial and answered < 5:
            print(f"WARNING: only {answered} model call{'' if answered == 1 else 's'} answered; "
                  "one call moves a rate by 20 points or more, so do not quote it as a "
                  "routing rate")
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
    """The repeat noise of a model arm, as its one console line; silent with one repeat."""
    noise = model_arm["noise"]
    if noise["n_repeats"] < 2:
        return
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
    """The model, noise and cost lines of an all-vs-all run; silent without ``--model``."""
    partial = model_arm.get("partial")
    if partial:
        answered = sum(model_arm.get("n_scored", {}).values())
        print(f"model    partial: {partial['calls_done']} of {partial['calls_planned']} calls, "
              f"{answered} answered")
        if answered < 5:
            print(f"WARNING: only {answered} model call{'' if answered == 1 else 's'} answered; "
                  "one call moves a rate by 20 points or more, so do not quote it as a "
                  "routing rate")
    if model_arm.get("noise"):
        print_noise(model_arm)
    if cost is not None:
        print_cost(cost)


def print_all_vs_all_summary(result: dict, n_field: int, n_skills: int, model_arm: dict,
                             cost: dict | None) -> None:
    """The summary of an all-vs-all run: the field line, the attractors, the model lines.

    Printed for a finished run and for one the cap stopped; the model line of the latter says how
    many calls it rests on.
    """
    print(f"all-vs-all: {n_field} skills in the field, {result['n_queries']} queries over "
          f"{n_skills} skills with queries")
    print_attractors(result)
    print_all_vs_all_model(model_arm, cost)


def exit_partial_all_vs_all(model_arm: dict, cost: dict | None, failed: int, sent: int,
                            out: Path) -> NoReturn:
    """The failure exit of an all-vs-all run where some model calls failed.

    The usage of the calls that did succeed still costs money and noise is still measured, so the
    noise and cost lines print before the exit; the partial result is already saved in ``out``.
    """
    print_all_vs_all_model(model_arm, cost)
    raise SystemExit(f"{failed} of {sent} model call(s) failed "
                     f"({model_arm['first_error']}); the partial result leaves the failed calls "
                     "out of model-arm scoring, and a query with a failed repeat out of the "
                     f"repeat-noise rates, is in {out}")


def partial_block(model_arm: dict) -> dict:
    """What a routing result gains when the cap stopped its model arm: ``status`` and ``partial``.

    ``status`` is how a routing record says what kind it is (``ok``, ``no_queries``), so a reader
    that checks it does not take a prefix of the run for a finished measurement.
    """
    return {"status": "partial", "partial": model_arm["partial"]} if "partial" in model_arm else {}


def _is_partial_file(path: Path) -> bool:
    """Whether ``path`` holds a partial result; a file this cannot read is not one."""
    try:
        earlier = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(earlier, dict) and ("partial" in earlier or earlier.get("status") == "partial")


def _takes_partial(candidate: Path) -> bool:
    """Whether a partial result may go to ``candidate``: nothing is there, or a regular file holding
    one (:func:`_wrote` tries the write). A FIFO or device is never opened: its read blocks (#516).
    A symlink, dangling or a loop, is not followed and is skipped: a write would land at its target.
    Any other error is raised, since every later name can fail the same way and the search would
    not end. A name too long does not reach here: :func:`_partial_destinations` shortens the stem."""
    try:
        mode = os.lstat(candidate).st_mode
    except FileNotFoundError:
        return True
    return stat.S_ISREG(mode) and _is_partial_file(candidate)


_UTF16_NAMES = os.name == "nt"


def _name_size(name: str) -> int:
    """``name``'s length in the units its file system limits: UTF-16 code units on Windows, else bytes."""
    if _UTF16_NAMES:
        return len(name.encode("utf-16-le", "surrogatepass")) // 2
    return len(os.fsencode(name))


_LONGEST_TAIL = ".partial999999.json"  # the numbered sidecar name's tail at a counter of six digits


def _name_limit(out: Path) -> int:
    """The file system's name limit beside ``out`` (``PC_NAME_MAX``), else 255."""
    try:
        name_max = os.pathconf(out.parent, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        return 255
    return name_max if name_max > 0 else 255  # -1: the limit is indeterminate


def _sidecar_stem(out: Path, tail: str = _LONGEST_TAIL) -> str:
    """``out``'s stem, as is when ``<stem><tail>`` fits the file system's name limit (``tail`` is
    ``.partial.json``, ``.partial2.json``, ...; by default the longest, a counter of six digits).
    Otherwise it is cut on a character boundary, measured in the file system's units
    (:func:`_name_size`; a non-UTF-8 byte is kept), and ends in a digest of the whole stem, so two
    long outs never share a sidecar. The cut leaves room for the longest tail, so every numbered
    name of one out has the same stem; only a limit too small for that cuts to this tail alone.
    A stem that fits but is as long as a cut one and ends in a digest-shaped tag (in either case,
    as a case-insensitive file system folds it) is cut again, so it never equals the cut name of
    another out. A limit with no room for the digest in ``tail``'s name raises ``ENAMETOOLONG``."""
    name_max, stem = _name_limit(out), out.with_suffix("").name
    fit, tag = name_max - len(tail), "-" + hashlib.sha256(os.fsencode(stem)).hexdigest()[:12]
    room = name_max - len(_LONGEST_TAIL)
    if room < len(tag):  # a small limit: cut for this tail alone
        room = fit
    if _name_size(stem) <= fit and not (  # a cut name is within 3 units of room
            _name_size(stem) > room - 4 and re.search(r"-[0-9a-f]{12}$", stem, re.IGNORECASE)):
        return stem
    if room < len(tag):
        raise OSError(errno.ENAMETOOLONG, f"a name limit of {name_max} leaves no room for a partial result's name", str(out))
    stem = stem[: room - len(tag)]
    while _name_size(stem) > room - len(tag):
        stem = stem[:-1]
    return stem + tag


def _partial_destinations(out: Path) -> Iterator[Path]:
    """Each of ``<out stem>.partial.json``, ``<out stem>.partial2.json``, ... in turn that is free
    or holds a partial result (:func:`_takes_partial`), so a partial result never replaces a
    finished one. A stem too long for a name to fit is shortened for it (:func:`_sidecar_stem`)."""
    number = 1
    while True:
        tail = ".partial.json" if number == 1 else f".partial{number}.json"
        candidate = out.with_name(_sidecar_stem(out, tail) + tail)
        if _takes_partial(candidate):
            yield candidate
        number += 1


def _wrote(path: Path, text: str) -> bool:
    """Write ``text`` to ``path``; False when the file is there and refuses the write. The write is
    the test, since ``os.access`` on Windows reads the read-only attribute and not the file's ACL.
    A refused new file is raised: the directory refuses it, and would refuse every later name."""
    try:
        path.write_text(text, encoding="utf-8")
    except PermissionError:
        if not path.exists():
            raise
        return False
    return True


def save_result(payload: dict, out: Path, model_arm: dict) -> Path:
    """Write a routing result and return where it went.

    ``model_arm`` is the arm the cap may have stopped: its ``partial`` block, if any, marks the
    result (:func:`partial_block`). A partial result goes to ``out`` unless ``out`` already holds
    something that is not itself a partial result (a finished measurement, or a file this cannot
    read): then it goes to the first sidecar beside it (:func:`_partial_destinations`) that is free
    or a writable partial result, so a cap does not replace a complete run with a prefix.
    """
    payload.update(partial_block(model_arm))
    text = json.dumps(payload, indent=2)
    if "partial" in payload and out.exists() and not _is_partial_file(out):
        return next(side for side in _partial_destinations(out) if _wrote(side, text))
    out.write_text(text, encoding="utf-8")
    return out


def stop_if_partial(payload: dict, out: Path, show: Callable[[], None]) -> None:
    """Exit non-zero when ``--max-usd`` stopped the run; the result is already saved in ``out``.

    ``show`` prints the summary and cost lines first, so the calls paid for are on the console.
    The partial block says how many of the planned model calls were made.
    """
    partial = payload.get("partial")
    if partial is None:
        return
    show()
    raise SystemExit(f"{partial['message']}. The partial result, {partial['calls_done']} of "
                     f"{partial['calls_planned']} model calls, is in {out}")


def warn_blind_skills(result: dict, queries_by_skill: dict[str, list[str]]) -> None:
    """Warn when the lexical arm scores none of a skill's queries: its own rate is then None."""
    lexical = result.get("lexical")
    if not lexical:
        return
    blind = [name for name in sorted(queries_by_skill) if lexical["n_scored"].get(name) == 0]
    if blind:
        print(f"WARNING: the lexical arm cannot score any query of {', '.join(blind)} "
              "(no content word in them), so their lexical own_rate is None", file=sys.stderr)


def warn_shared_queries(queries_by_skill: dict[str, list[str]]) -> None:
    """Warn on stderr when half or more of the queries are listed for several skills."""
    from dokimi.novelty.query_overlap import shared_queries_warning

    if shared := shared_queries_warning(queries_by_skill):
        print(f"WARNING: {shared}", file=sys.stderr)


def field_record(args: argparse.Namespace, competitors: list[Candidate], left_out: list[str],
                 dropped_queries: list[dict] | None = None, gold: Candidate | None = None) -> dict:
    """The field rule a routing result was measured under, saved next to the result.

    ``dropped_queries`` (all-vs-all only) lists each skill whose queries were dropped, with the
    reason and the flag that keeps it. ``spec_warnings`` lists ``gold`` (the skill under test,
    when given) as well as the field: a description over the 1024-character limit is kept and
    recorded there.
    """
    from dokimi.novelty.routing import over_limit_warnings

    record = {**{name: bool(getattr(args, name)) for name in ("include_disabled", "include_invalid")},
              "keep_copies_under": args.keep_copies_under,
              "competitors": [c.name for c in competitors], "left_out": left_out,
              "spec_warnings": (over_limit_warnings([gold], under_test=True) if gold else [])
              + over_limit_warnings(competitors)}
    if dropped_queries is not None:
        record["dropped_queries"] = dropped_queries
    return record
