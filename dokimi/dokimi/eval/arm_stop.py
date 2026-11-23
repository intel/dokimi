# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run-wide CLI failure (usage limit, no login) stops an arms run, and the question it stopped
keeps a cell for every arm: the arm that hit the error, the arms it completed, and ``not_asked`` for the rest."""

import threading
from typing import Any, Dict, Mapping, Optional, Sequence


class Halted(Exception):
    """Raised between arms of a question once another question has stopped the run."""


def halt_if_stopped(stop: Optional[threading.Event]) -> None:
    if stop is not None and stop.is_set():
        raise Halted()


class RunStopped(Exception):
    """A run-wide CLI failure: the run stops, and the question it stopped keeps its completed arms."""

    run_wide = True

    def __init__(self, cause: Exception, record: Dict[str, Any]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.record = record


def static_arm_kind(treatment: Any) -> Dict[str, Any]:
    """The ``arm_kind`` metadata of a treatment whose ``prepare()`` never returned."""
    t = getattr(treatment, "inner", treatment)  # a plugin wrapper
    subs = getattr(t, "treatments", None)  # a combined arm
    if isinstance(subs, list):
        return {"arm_kind": "combined",
                "treatments": [{"name": s.name, **static_arm_kind(s)} for s in subs]}
    return {"arm_kind": getattr(t, "arm_kind", None)}


def arm_kinds(treatments: Sequence[Any], reminder_suffix: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Each arm's :func:`static_arm_kind` by name, a session's reminder arms (*reminder_suffix*) after the others."""
    kinds = {t.name: static_arm_kind(t) for t in treatments}
    if reminder_suffix is not None:
        kinds.update({name + reminder_suffix: kind for name, kind in list(kinds.items())})
    return kinds


def stop_question(arms: Dict[str, Any], kinds: Mapping[str, Dict[str, Any]], current: str, exc: Exception) -> None:
    """Fill the stopped question's cells in place: ``current`` is the arm the error hit.

    *kinds* maps every arm name to its ``arm_kind`` metadata, so a stopped cell still says what arm it is.
    """
    arms[current] = {"error": f"generation_failed: {exc}", "answer": None, "metadata": dict(kinds.get(current, {}))}
    mark_not_asked(arms, kinds)


def mark_not_asked(arms: Dict[str, Any], kinds: Mapping[str, Dict[str, Any]]) -> None:
    """An arm the stop never reached says so, as every record has every arm."""
    for name, kind in kinds.items():
        arms.setdefault(name, {"error": "not_asked: the run stopped before this arm", "answer": None,
                               "metadata": dict(kind)})
