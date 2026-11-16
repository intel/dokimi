# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""What a run that ``--max-usd`` stops leaves behind, and what a run is expected to cost.

A stopped atoms, probe or analyze run is kept as ``<out stem>.partial.json``, beside the file it
would have written: a partial result never replaces a finished one, and the readers that take an
atoms file, a probe file or a ``novelty.json`` refuse a partial one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from dokimi.commands.novelty_routing_report import _partial_destinations


def partial_destination(out: Path) -> Path:
    """The first of ``<stem>.partial.json``, ``<stem>.partial2.json``, ... that is free or already
    holds a partial result, so a stopped run never replaces a finished one. The sidecar rules are
    the routing report's (a symlink is not followed, a FIFO is not read)."""
    return next(_partial_destinations(Path(out)))


def partial_write(out: Path, write: Callable[[Path], object]) -> Path:
    """Run ``write`` on the first sidecar beside ``out`` that takes a partial result. A read-only
    file that holds one refuses the write, and is passed over for the next name, as the routing
    report's save does; a new file that is refused is raised."""
    for dest in _partial_destinations(Path(out)):
        try:
            write(dest)
        except PermissionError:
            if not dest.exists():
                raise
            continue
        return dest
    raise RuntimeError("unreachable: the sidecar names never run out")


def exit_partial(summary: str, path: Path, note: str = "") -> NoReturn:
    """Stop with a message that names the partial file, as a run the cap stopped must."""
    raise SystemExit(f"{summary}; the partial result is in {path}{note}")


def response_cache_active() -> bool:
    """Whether the calls this run paid for are in the response cache (``DOKIMI_RESPONSE_CACHE``)."""
    from dokimi.caching import response_cache_dir, response_store_failed

    try:
        cache = response_cache_dir()
    except ValueError:  # the variable names a file: no call was cached
        return False
    # Every paid call has gone through the cache by now: a dir missing or read-only kept none,
    # and one whose write failed (a full disk) may have lost some.
    return cache is not None and os.access(cache, os.W_OK) and not response_store_failed(cache)


def replay_hint(reason: str = "spend_cap") -> str:
    """How a rerun gets the calls this run paid for back at $0: the response cache, which must
    have been set on the run that paid (every novelty call goes through it). A higher
    ``--max-usd`` is advised only for a ``spend_cap`` stop: a call with no price is refused
    whatever the cap, so that stop is cured by a price or by dropping the cap."""
    from dokimi.caching import RESPONSE_CACHE_ENV

    if response_cache_active():
        rerun = ("a higher --max-usd" if reason == "spend_cap"
                 else "the error fixed," if reason == "permanent_error"
                 else "the model priced, or without --max-usd,")
        return (f". Rerun with {rerun} and the same {RESPONSE_CACHE_ENV}: the calls "
                "already paid for replay at $0 (a cut-off or unusable reply is asked again)")
    return (f". {RESPONSE_CACHE_ENV} was not set on this run or could not be written, so a "
            "rerun pays for these calls again; set it to a writable directory before the next "
            "run to replay them at $0")


def save_analyze_partial(result, out: Path) -> NoReturn:
    """Keep what a stopped ``analyze`` paid for beside ``out`` and exit, naming the file.

    ``result`` is a :class:`~dokimi.novelty.analyze.AnalyzeResult` with ``partial`` set. The
    record goes to the sidecar rules of ``atoms`` and ``probe`` (never over a finished
    ``novelty.json``).
    """
    partial = result.partial
    cause = partial.get("cause")
    summary = f"{partial['message']} ({cause})" if cause else partial["message"]
    exit_partial(summary, write_run(result.artifact, out, partial),
                 replay_hint(partial["reason"]))


def write_run(payload: dict, out: Path, partial: dict | None = None) -> Path:
    """Write a run's record to ``out``; a run the cap stopped (``partial``) goes beside it."""
    if partial:
        payload.update(status="partial", partial=partial)
    text = json.dumps(payload, indent=2)
    out.parent.mkdir(parents=True, exist_ok=True)
    if not partial:
        out.write_text(text, encoding="utf-8")
        return out
    return partial_write(out, lambda dest: dest.write_text(text, encoding="utf-8"))


#: The probe record's summary blocks over the runs a partial file keeps. judge2 too: the
#: shared cap refuses the second judge, so its block is a stop record or empty.
PARTIAL_SUMMARIES = ("paired", "arms", "abstentions", "reversal_resisted",
                     "reversal_resisted_by_arm", "reversal_paired", "confidence_correlation",
                     "judge2")


def save_probe_run(payload: dict, out: Path, *, done: int, not_run: int,
                   stop: tuple[str, str] | None = None, ungraded: int = 0) -> Path:
    """Write a probe measurement; one the cap stopped keeps its ``done`` answers and exits.

    ``stop`` is the reason and message of the call the cap refused: a call with no known price
    is named as such, not as a ``--max-usd`` stop. ``ungraded`` answers were paid for and kept,
    but never graded, so the file is partial too."""
    if not not_run and not ungraded:
        return write_run(payload, out)
    reason, cause = stop or ("spend_cap", "")
    left = f"; {ungraded} of them not graded" if ungraded else ""
    if reason == "spend_cap":
        message = f"--max-usd stopped the probes with {done} of {done + not_run} answers{left}"
    else:
        message = f"the probes stopped with {done} of {done + not_run} answers{left}: {cause}"
    partial = {"reason": reason, "calls_done": done, "calls_planned": done + not_run,
               "message": message}
    # The jobs run arm by arm, so a cut leaves the arms uneven: the summaries over the answers
    # kept would read as results next to status "partial". The runs stay; the summaries go.
    for block in PARTIAL_SUMMARIES:
        if block in payload:
            payload[block] = None
    exit_partial(message, write_run(payload, out, partial))


def save_probe_measure(payload: dict, out: Path, result) -> Path:
    """Save a probe measurement from its :class:`~dokimi.novelty.measure.MeasureResult`."""
    return save_probe_run(payload, out, done=len(result.runs), not_run=result.not_run,
                          stop=result.stop, ungraded=result.ungraded)


def save_atoms_run(atoms, spans, out: Path, partial: dict | None, **record) -> Path:
    """Save an atom set (:func:`~dokimi.novelty.atoms.save_atoms`); a cap-stopped one goes beside
    ``out`` and exits."""
    from dokimi.novelty.atoms import save_atoms

    if not partial:
        save_atoms(atoms, spans, out, partial=None, **record)
        return out
    dest = partial_write(out, lambda side: save_atoms(atoms, spans, side, partial=partial,
                                                      **record))
    exit_partial(partial["message"], dest)


def verify_or_partial(sample, spans, client) -> dict | None:
    """Check the sampled atoms' faithfulness; a cap that stops the check keeps every atom,
    unchecked, as a partial result."""
    from dokimi.novelty.atoms import verify_atoms
    from dokimi.novelty.client import SpendCapReached

    try:
        verify_atoms(sample, spans, client=client)
    except SpendCapReached as exc:
        # Extraction may have left spans unread before the check ran: count those as unread.
        read = len(spans) - sum(span.unread for span in spans)
        message = (f"--max-usd stopped the faithfulness check with {read} of {len(spans)} "
                   "spans read") if exc.reason == "spend_cap" else (
                   f"the faithfulness check stopped with {read} of {len(spans)} spans read: "
                   f"{exc}")
        return {"reason": exc.reason, "stage": "verify", "spans_read": read,
                "spans_total": len(spans), "message": message}
    return None


def run_estimate(args: argparse.Namespace, settings, text: str, estimate_usd) -> tuple[dict, int]:
    """The run's estimate, with ``expected_usd`` at list prices, and the artifact's token count.

    ``estimate_usd`` is the command's pricing (``dokimi.novelty.cost.estimate_usd``), looked up
    by the caller at call time, so a test that patches ``cost.estimate_usd`` takes effect.
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
    if usd is None:  # estimate_usd has already said why
        return
    label = f"--tier {settings.tier}" if settings.tier else "default settings"
    cap = (f"--max-usd {args.max_usd:g} stops it at that cap" if args.max_usd is not None
           else "--max-usd caps it")
    low = " (a low projection for this size)" if n_tokens > FITTED_TOKENS_MAX else ""
    print(f"Expected ~${usd:.2f}{low} for this run at list prices ({label}); {cap}",
          file=sys.stderr)
