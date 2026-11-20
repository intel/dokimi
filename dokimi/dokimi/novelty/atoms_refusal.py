# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The sentence that refuses a partial atoms file (``load_atoms_record``), by the reason the run stopped."""

from __future__ import annotations

import unicodedata

#: U+115F, U+1160, U+3164, U+FFA0 (Hangul fillers) and U+2800 (braille blank): letters and a symbol that render blank.
_BLANK_FILLERS = frozenset("\u115f\u1160\u3164\uffa0\u2800")


def has_visible_text(message: str) -> bool:
    """Whether ``message`` has a letter, number, punctuation or symbol that shows (no mark, format, space or filler)."""
    return any(unicodedata.category(ch)[0] in "LNPS" and ch not in _BLANK_FILLERS for ch in message)


_STOPS = {
    "no_price": ("a call with no known price stopped the run", "Drop --max-usd to run unmetered"),
    "permanent_error": ("a permanent provider error stopped the run", "Fix the error and rerun"),
}


def partial_atoms_refusal(path, partial: dict) -> str:
    """Why ``path``, an atoms file whose ``partial`` block is ``partial``, is not a finished atom set.

    A ``no_price`` or ``permanent_error`` block names its stop in ``message``; one whose message is not
    text with a visible character (:func:`has_visible_text`) gets the reason's own text.
    A message that already ends with the reason's advice is not followed by it again.
    Any other reason is a ``--max-usd`` stop.
    """
    reason = partial.get("reason")
    if not isinstance(reason, str) or (stop := _STOPS.get(reason)) is None:  # a higher cap helps
        return (f"{path} is a partial atoms file: --max-usd stopped the run before it finished, so it is "
                "not a finished atom set. Rerun with a higher --max-usd, or without the cap")
    message = partial.get("message")
    if not isinstance(message, str) or not has_visible_text(message):
        message = stop[0]
    end = len(message)
    while end and (message[end - 1] == "." or message[end - 1].isspace()):
        end -= 1
    if message[:end].endswith(stop[1]):  # the stop's own text already ends with the advice
        return f"{path} is a partial atoms file: {message}; it is not a finished atom set."
    return f"{path} is a partial atoms file: {message}, so it is not a finished atom set. {stop[1]}"
