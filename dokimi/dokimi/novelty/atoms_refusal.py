# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The sentence that refuses a partial atoms file (``load_atoms_record``), by the reason the run stopped."""

from __future__ import annotations

_STOPS = {
    "no_price": ("a call with no known price stopped the run", "Drop --max-usd to run unmetered"),
    "permanent_error": ("a permanent provider error stopped the run", "Fix the error and rerun"),
}


def partial_atoms_refusal(path, partial: dict) -> str:
    """Why ``path``, an atoms file whose ``partial`` block is ``partial``, is not a finished atom set.

    A ``no_price`` or ``permanent_error`` block names its stop in ``message``; one whose message is not
    text with a visible character gets the reason's own text, so the sentence never has an empty slot.
    Any other reason is a ``--max-usd`` stop.
    """
    reason = partial.get("reason")
    if not isinstance(reason, str) or (stop := _STOPS.get(reason)) is None:  # a higher cap helps
        return (f"{path} is a partial atoms file: --max-usd stopped the run before it finished, so it is "
                "not a finished atom set. Rerun with a higher --max-usd, or without the cap")
    message = partial.get("message")
    if not isinstance(message, str) or not any(ch.isprintable() and not ch.isspace() for ch in message):
        message = stop[0]
    return f"{path} is a partial atoms file: {message}, so it is not a finished atom set. {stop[1]}"
