# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What `skill novelty probe`, `shorten` and `view` say about the ``atoms.json`` they read, and which
of its atoms `probe` and `shorten` measure."""

from __future__ import annotations

import argparse
import sys

from dokimi.utils import InputError


def warn_unread(spans: list, consequence: str) -> None:
    """Say on stderr that ``atoms.json`` records spans extraction never read.

    The record is the only trace of them after ``skill novelty atoms``, so each stage that
    reads the file repeats it, with what it means for that stage.
    """
    unread = [span for span in spans if span.unread]
    if unread:
        print(f"WARNING: {len(unread)} of {len(spans)} spans "
              f"({sum(span.tokens for span in unread)} tokens) were never read by "
              f"extraction (see `extraction` in the atoms file); {consequence}",
              file=sys.stderr)


def _capped_sample(saved: dict) -> str | None:
    """How an ``atoms.json`` that predates ``sampled_atom_ids`` holds only its probed atoms:
    ``"holds"``, ``"may hold"`` (the cap may not have cut any), or ``None``."""
    if "sampled_atom_ids" in saved:
        return None
    extraction = saved.get("extraction") or {}  # an object or absent: the loader checked
    if extraction.get("sampled"):
        return "holds"
    cap = saved.get("cap")
    if not isinstance(cap, int):
        return None
    reached = extraction.get("reached")
    if isinstance(reached, dict):
        return "may hold" if reached.get("spans") == reached.get("of") else "holds"
    # Before `reached` (#299) a document-order cap stopped at exactly `cap` atoms.
    return "may hold" if "reached" not in extraction and len(saved.get("atoms") or []) >= cap \
        else None


def holds_only_a_capped_sample(saved: dict) -> bool:
    """Whether ``atoms.json`` predates ``sampled_atom_ids`` and its cap may have left atoms out.

    Such a file holds only the atoms its run probed. A spread cap (#293) recorded
    ``extraction.sampled``; a document-order cap before it recorded ``cap`` and an
    ``extraction.reached`` object, which it wrote only when the cap bound. Even a reach at the
    input's last span (``report.reach_is_end``) may have cut that reply's later atoms, so it
    counts too, and so does a file from before ``reached`` (#299) with ``cap`` set and at least
    ``cap`` atoms, whose cap may have bound. A file this version wrote has the key, ``null``
    when its cap kept every atom.
    """
    return _capped_sample(saved) is not None


def warn_capped_sample(saved: dict, path) -> None:
    """Say on stderr that an older capped ``atoms.json`` indexes spans from its sample only (#371).

    When a document-order cap reached the input's last span, the file only *may* have lost atoms
    (those the cap cut from the last reply), and the warning says so; so it does for a capped
    file from before ``extraction.reached``, which cannot tell whether its cap bound.
    """
    kind = _capped_sample(saved)
    if kind is None:
        return
    reached = (saved.get("extraction") or {}).get("reached")
    holds = ("it holds only the atoms its run probed" if kind == "holds"
             else "it may hold only the atoms its run probed (its cap was met at the input's "
             "last span and may have cut that reply)" if reached
             else "it may hold only the atoms its run probed (it records no `extraction.reached`, "
             "so whether its cap bound is unknown)")
    print(f"WARNING: {path} was written by a capped `skill novelty atoms` run before atoms files "
          f"recorded their sample (`sampled_atom_ids`): {holds}, so spans whose atoms fell "
          "outside them read as carrying nothing and a shortening can cut them; re-run "
          "`skill novelty atoms` to index every atom", file=sys.stderr)


def _warn_unchecked(atoms: list, sample: list, stage: str, path) -> None:
    """Say on stderr how many atoms ``--all-atoms`` adds that were never checked for
    faithfulness: ``skill novelty atoms`` verifies only the cap's sample (#371)."""
    sampled = {atom.id for atom in sample}
    added = [atom for atom in atoms if atom.id not in sampled]
    unchecked = sum(1 for atom in added if atom.faithfulness == "unchecked")
    if unchecked:
        print(f"WARNING: {stage} --all-atoms measures {len(added)} atom(s) outside the sample "
              f"{path} records, {unchecked} of them with faithfulness \"unchecked\": their answer "
              "key was never checked against the text. To check every atom, re-run `skill novelty "
              "atoms --all-atoms`, a new uncapped extraction that verifies each atom it extracts",
              file=sys.stderr)


def sampling_record(args: argparse.Namespace, atoms: list, saved: dict) -> dict:
    """``probes.json``'s ``sampling``: the ``atoms`` measured, ``of`` the file's atoms, and
    whether ``--all-atoms`` was set, so a rate over the sample is not read as the file's."""
    return {"atoms": len(atoms), "of": len(saved["atoms"]), "all_atoms": bool(args.all_atoms)}


def measured_atoms(args: argparse.Namespace, atoms: list, saved: dict, stage: str) -> list:
    """The atoms ``stage`` measures: the cap's sample ``atoms.json`` records, or with
    ``--all-atoms`` every atom in it, reusing its extraction (#371).

    Spans are indexed from every atom either way; a sample that leaves atoms out is said on
    stderr, and so is an older capped file that holds only its sample. A sample the caller's
    filter emptied (shortening keeps the entry point's atoms) exits: it would score nothing.
    """
    from dokimi.novelty.atoms import recorded_sample

    warn_capped_sample(saved, args.atoms)
    sample = recorded_sample(saved, atoms)
    if args.all_atoms:
        _warn_unchecked(atoms, sample, stage, args.atoms)
    if args.all_atoms or len(sample) == len(atoms):
        return atoms
    if not sample and atoms:
        raise InputError(f"{stage}: {args.atoms} records a sample of {len(saved['sampled_atom_ids'])} "
                         f"atom(s), none of them among the {len(atoms)} of its {len(saved['atoms'])} "
                         f"atoms {stage} measures here, so it would score nothing; pass --all-atoms "
                         f"to measure those {len(atoms)}")
    print(f"NOTE {stage} measures the {len(sample)} atoms {args.atoms} records as the cap's "
          f"sample, of its {len(saved['atoms'])}; spans are indexed from all "
          f"{len(saved['atoms'])}. Pass "
          "--all-atoms to measure every atom, reusing the extraction", file=sys.stderr)
    return sample


def no_candidate_line(candidates: list, scored: list, atoms: list) -> str:
    """Why ``shorten`` offers nothing: every safe cut keeps the whole artifact, or none is safe."""
    if any(candidate.safe for candidate in candidates):
        return "No safe candidate is shorter than the artifact; its length is its content."
    unsampled = " and unsampled atom" if len(scored) < len(atoms) else ""
    return f"No safe candidate (none kept every protected span{unsampled})."


def _verdicts_hint(oracle, cache_dir, stop) -> str:
    """What a rerun can reuse after the cap stopped ``shorten``: only what is true for this run.

    A verdict is stored the moment it is adjudicated, so the stored ones are the oracle's
    escalations less the failed ones and the last call. That call was refused by a spent cap, or
    (``stop.reply`` set, an unpriced model) paid for and its verdict dropped unstored.
    """
    from dokimi.commands.novelty_budget import response_cache_active

    stored = max(0, oracle.escalations - oracle.errors - 1)
    dropped = stop.reply is not None
    rerun = ("no --max-usd" if stop.reason == "no_price" else
             "the error fixed" if stop.reason == "permanent_error" else "a higher --max-usd")
    tail = "; the call with no price was paid for and its verdict was not stored" if dropped else ""
    if cache_dir and stored:
        return (f"the retention verdicts adjudicated before the stop ({stored}) are stored in "
                f"{cache_dir}; rerun with {rerun} and the same --cache-dir to replay them at $0{tail}")
    # A call can be paid for and give no usable verdict (an unparseable reply): the client's
    # usage, not the verdict counts, says whether this run paid for any call.
    paid = oracle.client is not None and any(not use.replayed for use in oracle.client.usage)
    if cache_dir:
        return (f"no retention verdict is stored in {cache_dir} from this run"
                + (": the call with no price was paid for and its verdict was not stored" if dropped
                   else ": no paid call gave a usable verdict" if paid
                   else ": none had been adjudicated yet"))
    if stored and response_cache_active():
        return (f"the retention verdicts adjudicated in this run ({stored}) are in the response "
                f"cache (DOKIMI_RESPONSE_CACHE): rerun with {rerun} to replay them at $0{tail}")
    if stored:
        return (f"the retention verdicts adjudicated in this run ({stored}) were lost; "
                f"--cache-dir <dir> would keep them for a rerun{tail}")
    if dropped:
        return "the retention call with no price was paid for and its verdict was not stored"
    if paid:
        return "no paid retention call gave a usable verdict"
    return "no retention call had been paid for yet"


def score_candidates(args: argparse.Namespace, spans: list, atoms: list, scored: list):
    """The shortening candidates scored by a retention oracle, and that oracle.

    A stop at ``--max-usd`` exits with a message that says no candidate table and no ``--out``
    file was written and what a rerun can reuse; a table scored on part of the verdicts is not made.
    """
    from pathlib import Path

    from dokimi.novelty import cost
    from dokimi.novelty.client import SpendCapReached
    from dokimi.novelty.retention import RetentionOracle
    from dokimi.novelty.shorten import build_candidates

    client = cost.client(args.extract_model or args.model, args.provider, "oracle", args) \
        if not args.lexical_only else None
    oracle = RetentionOracle(client=client, cache_dir=Path(args.cache_dir) if args.cache_dir else None)
    try:
        candidates = build_candidates(spans, atoms, oracle=oracle, budgets=tuple(args.budgets),
                                      seed=args.seed, scored_atoms=scored)
    except SpendCapReached as exc:
        raise SystemExit(f"{exc}; nothing was written (no candidate table and no --out file); "
                         f"{_verdicts_hint(oracle, args.cache_dir, exc)}") from None
    return candidates, oracle


def warn_answer_key(saved: dict, answerer, judge) -> None:
    """Warn when the saved answer key was written by the model under measurement, or by the judge's model or family."""
    from dokimi.novelty.client import _REMEDY_PROBE, warn_extractor_judge
    from dokimi.utils import same_model

    extractor = saved.get("extractor")
    if not isinstance(extractor, str):
        return  # a null model in an artifact names no model: nothing to compare
    if extractor == answerer.model or same_model(extractor, answerer.model):
        print(f"WARNING: {answerer.model} extracted these atoms, so the model under measurement "
              "wrote its own answer key; write the atoms file again with another extractor (atoms --extract-model)",
              file=sys.stderr)
    provider = saved.get("extractor_provider")  # None in a record from before it was saved
    warn_extractor_judge(extractor, judge, provider if isinstance(provider, str) else "",
                         remedy=_REMEDY_PROBE)
