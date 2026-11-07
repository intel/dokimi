# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a run that ``--max-usd`` stops leaves behind, and what a run is expected to cost.

A stopped atoms or probe run is kept as ``<out stem>.partial.json``, beside the file it would
have written: a partial result never replaces a finished one, and the readers that take an
atoms file refuse a partial one.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn


def is_partial_file(path: Path) -> bool:
    """Whether ``path`` holds a partial result; a file this cannot read is not one."""
    try:
        earlier = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(earlier, dict) and "partial" in earlier


def partial_destination(out: Path) -> Path:
    """The first of ``<stem>.partial.json``, ``<stem>.partial2.json``, ... that is free or already
    holds a partial result, so a stopped run never replaces a finished one."""
    base = Path(out)
    candidate, number = base.with_name(f"{base.stem}.partial.json"), 2
    while candidate.exists() and not is_partial_file(candidate):
        candidate, number = base.with_name(f"{base.stem}.partial{number}.json"), number + 1
    return candidate


def exit_partial(summary: str, path: Path) -> NoReturn:
    """Stop with a message that names the partial file, as a run the cap stopped must."""
    raise SystemExit(f"{summary}; the partial result is in {path}")


def write_run(payload: dict, out: Path, partial: dict | None = None) -> Path:
    """Write a run's record to ``out``; a run the cap stopped (``partial``) goes beside it."""
    dest = partial_destination(out) if partial else out
    if partial:
        payload.update(status="partial", partial=partial)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return dest


def save_probe_run(payload: dict, out: Path, *, done: int, not_run: int) -> Path:
    """Write a probe measurement; one the cap stopped keeps its ``done`` answers and exits."""
    if not not_run:
        return write_run(payload, out)
    message = f"--max-usd stopped the probes with {done} of {done + not_run} answers"
    partial = {"reason": "spend_cap", "calls_done": done, "calls_planned": done + not_run,
               "message": message}
    exit_partial(message, write_run(payload, out, partial))


def save_atoms_run(atoms, spans, out: Path, partial: dict | None, **record) -> Path:
    """Save an atom set (:func:`~dokimi.novelty.atoms.save_atoms`); a cap-stopped one goes beside
    ``out`` and exits."""
    from dokimi.novelty.atoms import save_atoms

    dest = partial_destination(out) if partial else out
    save_atoms(atoms, spans, dest, partial=partial, **record)
    if partial:
        exit_partial(partial["message"], dest)
    return dest


def verify_or_partial(sample, spans, client) -> dict | None:
    """Check the sampled atoms' faithfulness; a cap that stops the check keeps every atom,
    unchecked, as a partial result."""
    from dokimi.novelty.atoms import verify_atoms
    from dokimi.novelty.client import SpendCapReached

    try:
        verify_atoms(sample, spans, client=client)
    except SpendCapReached:
        # Extraction may have left spans unread before the check ran: count those as unread.
        read = len(spans) - sum(span.unread for span in spans)
        return {"reason": "spend_cap", "stage": "verify", "spans_read": read,
                "spans_total": len(spans),
                "message": (f"--max-usd stopped the faithfulness check with {read} of "
                            f"{len(spans)} spans read")}
    return None


def run_estimate(args: argparse.Namespace, settings, text: str, estimate_usd) -> tuple[dict, int]:
    """The run's estimate, with ``expected_usd`` at list prices, and the artifact's token count.

    ``estimate_usd`` is the command's pricing (``dokimi.commands.novelty._estimate_usd``), passed
    in because that module imports this one.
    """
    from dokimi.novelty.measure import parse_arms
    from dokimi.novelty.spans import segment
    from dokimi.utils import InputError

    try:
        arms = parse_arms(args.arms)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    spans = segment(text)
    n_tokens = sum(span.tokens for span in spans)
    n_arms = len(arms)
    estimate = settings.estimate(
        len(spans), n_tokens=n_tokens, budgets=tuple(args.budgets), n_arms=n_arms
    )
    estimate["expected_usd"] = estimate_usd(
        args, n_tokens, estimate["estimate"]["by_role"], settings.tier,
        context_share=(n_arms - 1 - ("no_artifact_repeat" in args.arms)) / n_arms,
    )
    return estimate, n_tokens


def print_run_estimate(args: argparse.Namespace, settings, text: str, estimate_usd) -> None:
    """Name what the run is expected to cost before the first paid call (stderr)."""
    from dokimi.novelty.tiers import FITTED_TOKENS_MAX

    estimate, n_tokens = run_estimate(args, settings, text, estimate_usd)
    usd = estimate["expected_usd"]
    if usd is None:  # _estimate_usd has already said why
        return
    label = f"--tier {settings.tier}" if settings.tier else "default settings"
    cap = (f"--max-usd {args.max_usd:g} stops it at that cap" if args.max_usd is not None
           else "--max-usd caps it")
    low = " (a low projection for this size)" if n_tokens > FITTED_TOKENS_MAX else ""
    print(f"Expected ~${usd:.2f}{low} for this run at list prices ({label}); {cap}",
          file=sys.stderr)
