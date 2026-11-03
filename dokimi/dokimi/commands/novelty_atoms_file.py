# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What `novelty probe`, `shorten` and `view` say about the ``atoms.json`` they read, and which
of its atoms `probe` and `shorten` measure."""

from __future__ import annotations

import argparse
import sys


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
    """Whether ``atoms.json`` predates ``sampled_atom_ids`` and its cap left atoms out.

    Such a file holds only the atoms its run probed. A spread cap (#293) recorded
    ``extraction.sampled``; a document-order cap before it recorded ``cap`` and an
    ``extraction.reached`` short of the input's last span, as ``report.reach_is_end`` tells
    them apart. A file this version wrote has the key, ``null`` when its cap kept every atom.
    """
    if "sampled_atom_ids" in saved:
        return False
    extraction = saved.get("extraction") or {}  # an object or absent: the loader checked
    if extraction.get("sampled"):
        return True
    reached = extraction.get("reached")
    return (saved.get("cap") is not None and isinstance(reached, dict)
            and reached.get("spans") != reached.get("of"))


def warn_capped_sample(saved: dict, path) -> None:
    """Say on stderr that an older capped ``atoms.json`` indexes spans from its sample only (#371)."""
    if holds_only_a_capped_sample(saved):
        print(f"WARNING: {path} was written by a capped `novelty atoms` run before atoms files "
              "recorded their sample (`sampled_atom_ids`): it holds only the atoms its run "
              "probed, so spans whose atoms fell outside them read as carrying nothing and a "
              "shortening can cut them; re-run `novelty atoms` to index every atom",
              file=sys.stderr)


def measured_atoms(args: argparse.Namespace, atoms: list, saved: dict, stage: str) -> list:
    """The atoms ``stage`` measures: the cap's sample ``atoms.json`` records, or with
    ``--all-atoms`` every atom in it, reusing its extraction (#371).

    Spans are indexed from every atom either way; a sample that leaves atoms out is said on
    stderr, and so is an older capped file that holds only its sample.
    """
    from dokimi.novelty.atoms import recorded_sample

    warn_capped_sample(saved, args.atoms)
    sample = recorded_sample(saved, atoms)
    if args.all_atoms or len(sample) == len(atoms):
        return atoms
    print(f"NOTE {stage} measures the {len(sample)} atoms {args.atoms} records as the cap's "
          f"sample, of its {len(atoms)}; spans are indexed from all {len(atoms)}. Pass "
          "--all-atoms to measure every atom, reusing the extraction", file=sys.stderr)
    return sample
