# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Loading the artifact, competitors and field for `novelty routing`, failing with InputError."""

from __future__ import annotations

import argparse
import sys

from dokimi.novelty.routing import Candidate
from dokimi.utils import InputError


def routing_card(ref: str) -> tuple[Candidate, str]:
    """An artifact or competitor reference as the selector sees it: ``(candidate, body)``."""
    from dokimi.novelty.routing import load_candidate

    candidate, body, warning = load_candidate(ref)
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
    return candidate, body


def routing_field(args: argparse.Namespace, exclude: set[str],
                   left_out: list[str] | None = None) -> list[Candidate]:
    """The field ``--competitors`` and ``--catalog`` name, without the names in ``exclude``.

    What was left out of the field, and why, is appended to ``left_out`` when given.

    Raises:
        OSError, ValueError, RecursionError: A reference or the catalog cannot be loaded.
    """
    from dokimi.novelty.routing import (
        catalog_competitors,
        disables_model_invocation,
        non_skill_reason,
        skill_file,
    )

    competitors = []
    for ref in args.competitors or []:
        reason = non_skill_reason(ref)
        if reason:
            print(f"WARNING: skipped competitor {ref}: {reason}", file=sys.stderr)
            if left_out is not None:
                left_out.append(f"{ref}: {reason}")
            continue
        candidate = routing_card(ref)[0]
        ref_file = skill_file(ref)
        if (ref_file and not args.include_disabled
                and disables_model_invocation(ref_file)):
            print(f"WARNING: skipped competitor {ref}: disable-model-invocation "
                  "(--include-disabled keeps it)", file=sys.stderr)
            if left_out is not None:
                left_out.append(f"{ref}: disable-model-invocation")
            continue
        competitors.append(candidate)
    if args.catalog:
        notes: list[str] = []
        catalog = catalog_competitors(args.catalog,
                                      exclude | {c.name for c in competitors}, notes,
                                      include_disabled=args.include_disabled)
        for note in notes:
            print(f"WARNING: {note}", file=sys.stderr)
        if left_out is not None:
            left_out.extend(notes)
        no_description = [c.name for c in catalog if not c.description]
        if no_description:
            print(f"WARNING: {len(no_description)} catalog skill(s) have no description, "
                  f"so their card is a name only: {', '.join(sorted(no_description))}",
                  file=sys.stderr)
        print(f"--catalog {args.catalog}: {len(catalog)} competitors", file=sys.stderr)
        competitors += catalog
    return competitors


def load_routing_field(
    args: argparse.Namespace,
) -> tuple[Candidate, str, list[Candidate], list[str]]:
    """The gold card, its body, the competitors and the ones left out, or InputError naming the cause."""
    from dokimi.novelty.routing import disables_model_invocation, skill_file

    try:
        gold, body = routing_card(args.artifact)
        artifact_file = skill_file(args.artifact)
        if artifact_file and disables_model_invocation(artifact_file):
            print(f"WARNING: {args.artifact} sets disable-model-invocation, so no harness lists "
                  "it and the model cannot select it; its routing rate is not a harness rate",
                  file=sys.stderr)
        left_out: list[str] = []
        competitors = routing_field(args, {gold.name}, left_out)
    except InputError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"cannot load an artifact: {exc}") from exc
    return gold, body, competitors, left_out
