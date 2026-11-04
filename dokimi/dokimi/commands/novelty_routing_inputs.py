# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Loading the artifact, competitors and field for `novelty routing`, failing with InputError."""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.novelty.routing import Candidate


def routing_card(ref: str) -> tuple[Candidate, str]:
    """An artifact or competitor reference as the selector sees it: ``(candidate, body)``."""
    from dokimi.novelty.routing import load_candidate

    candidate, body, warning = load_candidate(ref)
    if warning:
        print(f"WARNING: {warning}", file=sys.stderr)
    return candidate, body


def named_competitors(args: argparse.Namespace, left_out: list[str] | None,
                      dropped: dict[str, set[str]] | None) -> list[Candidate]:
    """The ``--competitors`` references that stay in the field, each as a candidate."""
    from dokimi.novelty.routing import competitor_left_out, left_out_rules, non_skill_reason

    competitors = []
    for ref in args.competitors or []:
        reason = non_skill_reason(ref)
        if reason:
            print(f"WARNING: skipped competitor {ref}: {reason}", file=sys.stderr)
            if left_out is not None:
                left_out.append(f"{ref}: {reason}")
            continue
        candidate = routing_card(ref)[0]
        reason = competitor_left_out(ref, include_disabled=args.include_disabled,
                                     include_invalid=args.include_invalid)
        if reason:
            print(f"WARNING: skipped competitor {ref}: {reason}", file=sys.stderr)
            if left_out is not None:
                left_out.append(f"{ref}: {reason}")
            if dropped is not None:
                dropped.setdefault(candidate.name, set()).update(
                    left_out_rules(ref, reason, include_invalid=args.include_invalid))
            continue
        competitors.append(candidate)
    return competitors


def routing_field(args: argparse.Namespace, exclude: set[str],
                  left_out: list[str] | None = None,
                  dropped: dict[str, set[str]] | None = None,
                  conflicts: dict[str, list[str]] | None = None,
                  unloaded: dict[str, str] | None = None) -> list[Candidate]:
    """The field ``--competitors`` and ``--catalog`` name, without the names in ``exclude``.

    What was left out of the field, and why, is appended to ``left_out`` when given. Each
    name a rule leaves out maps to its rules in ``dropped`` (when given), and each name still in
    conflict maps to the paths of its copies in ``conflicts`` (when given). Each declared catalog
    skill that failed to load maps to why in ``unloaded`` (when given).

    Raises:
        InputError: ``--keep-copies-under`` is given without ``--catalog``, or is not a directory
            inside it.
        OSError, ValueError, RecursionError: A reference or the catalog cannot be loaded.
    """
    from dokimi.novelty.catalog_keep import check_keep_copies_under
    from dokimi.novelty.routing import catalog_competitors

    if args.keep_copies_under is not None:
        if not args.catalog:
            raise InputError("--keep-copies-under needs --catalog: it picks between the copies of "
                             "a catalog")
        try:
            check_keep_copies_under(args.catalog, args.keep_copies_under)
        except ValueError as exc:
            raise InputError(str(exc)) from exc
    competitors = named_competitors(args, left_out, dropped)
    if args.catalog:
        notes: list[str] = []
        catalog = catalog_competitors(args.catalog, exclude | {c.name for c in competitors},
                                      notes, include_disabled=args.include_disabled,
                                      include_invalid=args.include_invalid, dropped=dropped,
                                      keep_copies_under=args.keep_copies_under,
                                      conflicts=conflicts, unloaded=unloaded)
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


def load_all_vs_all_field(args: argparse.Namespace, left_out: list[str],
                          dropped: dict[str, set[str]], conflicts: dict[str, list[str]],
                          unloaded: dict[str, str]) -> list[Candidate]:
    """The all-vs-all field, or InputError naming the cause."""
    try:
        return routing_field(args, set(), left_out, dropped, conflicts, unloaded)
    except InputError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"cannot load the field: {exc}") from exc


def load_routing_field(
    args: argparse.Namespace,
) -> tuple[Candidate, str, list[Candidate], list[str], dict[str, list[str]]]:
    """The gold card, its body, the competitors, the ones left out and the names in conflict.

    The last is each conflicting name with the paths of its copies, or InputError naming the cause.
    """
    from dokimi.novelty.routing import disables_model_invocation, skill_file

    try:
        gold, body = routing_card(args.artifact)
        artifact_file = skill_file(args.artifact)
        if artifact_file and disables_model_invocation(artifact_file):
            print(f"WARNING: {args.artifact} sets disable-model-invocation, so no harness lists "
                  "it and the model cannot select it; its routing rate is not a harness rate",
                  file=sys.stderr)
        left_out: list[str] = []
        conflicts: dict[str, list[str]] = {}
        competitors = routing_field(args, {gold.name}, left_out, conflicts=conflicts)
    except InputError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"cannot load an artifact: {exc}") from exc
    return gold, body, competitors, left_out, conflicts
