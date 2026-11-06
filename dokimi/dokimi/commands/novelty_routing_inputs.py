# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Loading the artifact, competitors and field for `novelty routing`, failing with InputError."""

from __future__ import annotations

import argparse
import errno
import os
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dokimi.utils import InputError

if TYPE_CHECKING:
    from dokimi.novelty.routing import Candidate


def _refuse_a_non_file(path: Path, label: str, hint: str) -> None:
    """Refuse a FIFO or device, which would block or swallow the write, and a symlink loop."""
    try:
        mode = os.stat(path).st_mode
    except OSError as exc:
        # Windows reports a loop as WinError 1921 (ERROR_CANT_RESOLVE_FILENAME), not ELOOP.
        if exc.errno == errno.ELOOP or getattr(exc, "winerror", None) == 1921:
            raise InputError(f"{label} is a symlink loop, so the file cannot be written{hint}") from exc
        return  # missing, or under a non-directory: checked_out_path names it
    if not stat.S_ISREG(mode):
        raise InputError(f"{label} is not a regular file, so the results cannot be written to it{hint}")


def checked_out_path(out: str | None, command: str, default: str | None = None) -> Path:
    """The ``--out`` file of a routing run, refused before any model call when it cannot be written.

    The file must not be a directory. Its missing parent directories are fine: the writer creates
    them. But the nearest existing ancestor must be a directory the run can write into, and an
    existing file must itself be writable. A symlink is checked where it points: the writer
    follows it, and creates only the link's own parent, not the target's.

    With no ``out`` and a ``default``, the default path is checked, and a refusal names it as the
    default output and asks for ``--out``, since the user passed none.

    Raises:
        InputError: ``--out`` is missing (and no default), is a directory, sits under a path that is not a
            directory, is a symlink into a missing directory or a symlink loop, is not a regular
            file (a FIFO, a device), or is not writable.
    """
    chosen = out or default
    if not chosen:
        raise InputError(f"{command} needs --out <file>")
    label = f"--out {out}" if out else f"the default output {default}"
    hint = "" if out else " (pass --out <file>)"
    path = Path(chosen)
    if path.is_dir():
        fix = f"pass a file path, e.g. {path / 'routing.json'}" if out else "pass --out <file>"
        raise InputError(f"{label} is a directory: {fix}")
    _refuse_a_non_file(path, label, hint)
    # realpath follows a symlink even when it dangles, so the checks below see the file that is written.
    target = Path(os.path.realpath(path))
    if path.is_symlink() and not target.parent.exists():
        raise InputError(f"{label} is a symlink to {target}, whose directory does not exist: "
                         f"the writer creates only the link's own directory{hint}")
    # A dangling symlink among the parents: the writer's mkdir of that parent fails on it.
    for parent in path.parents:
        if parent.is_symlink() and not parent.exists():
            raise InputError(f"{label}: {parent} is a dangling symlink, so the writer cannot create "
                             f"its directory{hint}")
    ancestor = next((p for p in target.parents if p.exists()), target.parent)
    if not ancestor.is_dir():
        raise InputError(f"{label}: {ancestor} is not a directory, so the file cannot be created under it{hint}")
    if target.exists():
        writable = os.access(target, os.W_OK)
    else:
        writable = os.access(ancestor, os.W_OK | os.X_OK)
    if not writable:
        raise InputError(f"{label} is not writable{hint}")
    return path


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
                  dropped: dict[str, set[str]] | None = None) -> list[Candidate]:
    """The field ``--competitors`` and ``--catalog`` name, without the names in ``exclude``.

    What was left out of the field, and why, is appended to ``left_out`` when given. Each
    name a rule leaves out maps to its rules in ``dropped`` (when given).

    Raises:
        OSError, ValueError, RecursionError: A reference or the catalog cannot be loaded.
    """
    from dokimi.novelty.routing import catalog_competitors

    competitors = named_competitors(args, left_out, dropped)
    if args.catalog:
        notes: list[str] = []
        catalog = catalog_competitors(args.catalog, exclude | {c.name for c in competitors},
                                      notes, include_disabled=args.include_disabled,
                                      include_invalid=args.include_invalid, dropped=dropped)
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
                          dropped: dict[str, set[str]]) -> list[Candidate]:
    """The all-vs-all field, or InputError naming the cause."""
    try:
        return routing_field(args, set(), left_out, dropped)
    except InputError:
        raise
    except (OSError, ValueError, RecursionError) as exc:
        raise InputError(f"cannot load the field: {exc}") from exc


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
