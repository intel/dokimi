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


def measured_atoms(args: argparse.Namespace, atoms: list, saved: dict, stage: str) -> list:
    """The atoms ``stage`` measures: the cap's sample ``atoms.json`` records, or with
    ``--all-atoms`` every atom in it, reusing its extraction (#371).

    Spans are indexed from every atom either way; a sample that leaves atoms out is said on stderr.
    """
    from dokimi.novelty.atoms import recorded_sample

    sample = recorded_sample(saved, atoms)
    if args.all_atoms or len(sample) == len(atoms):
        return atoms
    print(f"NOTE {stage} measures the {len(sample)} atoms {args.atoms} records as the cap's "
          f"sample, of its {len(atoms)}; spans are indexed from all {len(atoms)}. Pass "
          "--all-atoms to measure every atom, reusing the extraction", file=sys.stderr)
    return sample
