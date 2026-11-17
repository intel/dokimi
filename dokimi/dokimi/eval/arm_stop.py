# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""A run-wide CLI failure (usage limit, no login) stops an arms run, and the question it stopped
keeps a cell for every arm: the arm that hit the error, the arms it completed, and ``not_asked`` for the rest."""

from typing import Any, Dict, Sequence


class RunStopped(Exception):
    """A run-wide CLI failure: the run stops, and the question it stopped keeps its completed arms."""

    run_wide = True

    def __init__(self, cause: Exception, record: Dict[str, Any]) -> None:
        super().__init__(str(cause))
        self.cause = cause
        self.record = record


def stop_question(arms: Dict[str, Any], arm_names: Sequence[str], current: str, exc: Exception) -> None:
    """Fill the stopped question's cells in place: ``current`` is the arm the error hit."""
    arms[current] = {"error": f"generation_failed: {exc}", "answer": None, "metadata": {}}
    mark_not_asked(arms, arm_names)


def mark_not_asked(arms: Dict[str, Any], arm_names: Sequence[str]) -> None:
    """An arm the stop never reached says so, as every record has every arm."""
    for name in arm_names:
        arms.setdefault(name, {"error": "not_asked: the run stopped before this arm", "answer": None,
                               "metadata": {}})
