# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What `novelty probe`, `shorten` and `view` say about the ``atoms.json`` they read, and which
of its atoms `probe` and `shorten` measure."""

from __future__ import annotations

import argparse
import sys

from dokimi.exit_codes import usage_exit


def warn_unread(spans: list, consequence: str) -> None:
    """Say on stderr that ``atoms.json`` records spans extraction never read.

    The record is the only trace of them after ``novelty atoms``, so each stage that
    reads the file repeats it, with what it means for that stage.
    """
    unread = [span for span in spans if span.unread]
    if unread:
        print(f"WARNING: {len(unread)} of {len(spans)} spans "
              f"({sum(span.tokens for span in unread)} tokens) were never read by "
              f"extraction (see `extraction` in the atoms file); {consequence}",
              file=sys.stderr)


def holds_only_a_capped_sample(saved: dict) -> bool:
    """Whether ``atoms.json`` predates ``sampled_atom_ids`` and its cap may have left atoms out.

    Such a file holds only the atoms its run probed. A spread cap (#293) recorded
    ``extraction.sampled``; a document-order cap before it recorded ``cap`` and an
    ``extraction.reached`` object, which it wrote only when the cap bound. Even a reach at the
    input's last span (``report.reach_is_end``) may have cut that reply's later atoms, so it
    counts too. A file this version wrote has the key, ``null`` when its cap kept every atom.
    """
    if "sampled_atom_ids" in saved:
        return False
    extraction = saved.get("extraction") or {}  # an object or absent: the loader checked
    if extraction.get("sampled"):
        return True
    return saved.get("cap") is not None and isinstance(extraction.get("reached"), dict)


def warn_capped_sample(saved: dict, path) -> None:
    """Say on stderr that an older capped ``atoms.json`` indexes spans from its sample only (#371).

    When a document-order cap reached the input's last span, the file only *may* have lost atoms
    (those the cap cut from the last reply), and the warning says so.
    """
    if not holds_only_a_capped_sample(saved):
        return
    extraction = saved.get("extraction") or {}
    reached = extraction.get("reached") or {}
    holds =("it may hold only the atoms its run probed (its cap was met at the input's last "
             "span and may have cut that reply)" if not extraction.get("sampled")
             and reached.get("spans") == reached.get("of")
             else "it holds only the atoms its run probed")
    print(f"WARNING: {path} was written by a capped `novelty atoms` run before atoms files "
          f"recorded their sample (`sampled_atom_ids`): {holds}, so spans whose atoms fell "
          "outside them read as carrying nothing and a shortening can cut them; re-run "
          "`novelty atoms` to index every atom", file=sys.stderr)


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
    if args.all_atoms or len(sample) == len(atoms):
        return atoms
    if not sample and atoms:
        usage_exit(f"{stage}: {args.atoms} records a sample of {len(saved['sampled_atom_ids'])} "
                   f"atom(s), none of them among the {len(atoms)} of its {len(saved['atoms'])} "
                   f"atoms {stage} measures here, so it would score nothing; pass --all-atoms "
                   f"to measure those {len(atoms)}")
    print(f"NOTE {stage} measures the {len(sample)} atoms {args.atoms} records as the cap's "
          f"sample, of its {len(atoms)}; spans are indexed from all {len(atoms)}. Pass "
          "--all-atoms to measure every atom, reusing the extraction", file=sys.stderr)
    return sample
