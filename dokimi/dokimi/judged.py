# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The judged-reply path: :func:`dokimi.llm.ask_parsed` with the response cache's replay.

Every judged reply (the judge, the panel, the pairwise judge and the checklist)
goes through :func:`judged_reply`. It lives beside :mod:`dokimi.llm` rather than in
it, because that module is over its line-count ratchet.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple, TypeVar

from dokimi import caching
from dokimi.llm import ATTEMPTS, Ask, ask_parsed

T = TypeVar("T")

#: The redraw reminder for a reply format the judge's JSON reminder does not fit.
REDRAW_REMINDER = (
    "\n\nReminder (re-draw {n} of 2): your previous reply could not be parsed. "
    "Reply again in exactly the output format the prompt asks for."
)


def _accepts(parse: Callable[[str], Any]) -> Callable[[str], bool]:
    """The response cache's ``cache_if``: a reply is kept only if *parse* reads it."""
    def parses(raw: str) -> bool:
        try:
            return parse(raw) is not None
        except Exception:
            return False
    return parses


def judged_reply(ask: Ask, prompt: str, parse: Callable[[str], Optional[T]], *,
                 pinned: bool, draw: int = 0, label: str = "LLM",
                 reminder: str = REDRAW_REMINDER,
                 on_redraw: Optional[Callable[[], None]] = None) -> Tuple[T, str]:
    """:func:`~dokimi.llm.ask_parsed` with the response cache's replay and the redraw policy.

    The path a judged reply takes (the validity scorer is not on it), so the caller supplies the prompt,
    the parser and the sampling state. Two things happen before and around the redraw loop:

    - **Replay.** With the response cache on, a rerun whose stored attempt is past
      the first ones starts at that attempt: the unparseable attempts before it were
      never stored and would otherwise be asked live again. See
      :func:`dokimi.caching.first_replayed`.
    - **Reprompt.** At *pinned* sampling the same prompt returns the same reply, so
      each redraw appends *reminder* (formatted with the attempt number). Unpinned,
      a redraw is a new sample of the plain prompt.

    *on_redraw* is called once, when the loop first asks a redraw. Each attempt is
    still its own draw, so the cache keys are those :func:`~dokimi.llm.ask_parsed` gives.

    Raises:
        Unparseable: no attempt parsed. Errors from *ask* itself propagate as-is.
    """
    reprompt: Optional[Callable[[str, int], str]] = (
        (lambda text, n: text + reminder.format(n=n)) if pinned else None)

    def text_of(attempt: int) -> str:
        return reprompt(prompt, attempt) if reprompt and attempt else prompt

    first, held = caching.first_replayed(
        ATTEMPTS, lambda i: ask(text_of(i), draw=draw * ATTEMPTS + i, cache_if=_accepts(parse))) or (0, None)
    base, first_redraw = draw * ATTEMPTS, max(first, 1)

    def asked(text: str, *, draw: int, cache_if: Callable[[str], bool]) -> str:
        attempt = draw - base  # ask_parsed draws attempt i as draw * ATTEMPTS + i
        if on_redraw is not None and attempt == first_redraw:
            on_redraw()
        if attempt == first and held is not None:
            return held
        return ask(text, draw=draw, cache_if=cache_if)

    return ask_parsed(asked, prompt, parse, draw=draw, label=label, start=first, reprompt=reprompt)
