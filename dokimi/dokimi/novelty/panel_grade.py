# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Grading with a judge panel: the checks made before a paid call and one answer's votes."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from dokimi.files import InputError
from .client import LLMClient, SpendCapReached, same_client
from .panel import RULES, aggregate as aggregate_votes


def check_judges(members: Sequence[LLMClient], aggregate: str,
                 check_one: Callable[[LLMClient], None]) -> None:
    """Refuse an unknown rule, a judge ``check_one`` refuses, or one model twice (it would vote twice)."""
    if aggregate not in RULES:
        raise InputError(f"unknown judge aggregation rule {aggregate!r}; known: {list(RULES)}")
    for i, member in enumerate(members):
        check_one(member)
        if any(same_client(member, other) for other in members[:i]):
            raise InputError(f"judge {member.model!r} is the same model as another judge")


def grade_panel(grade: Callable[[LLMClient], str], judges: Sequence[LLMClient], aggregate: str,
                errored: str) -> tuple[str, dict[str, str] | None]:
    """One answer's verdict and, with a panel, each judge's verdict by model; a failed judge is no vote."""
    if len(judges) == 1:
        return grade(judges[0]), None
    by_judge: dict[str, str] = {}
    try:
        for m in judges:
            by_judge[m.model] = grade(m)
    except SpendCapReached as exc:
        exc.panel_votes = by_judge
        raise
    votes = [v for v in by_judge.values() if v != errored]
    return aggregate_votes(votes, aggregate) or errored, by_judge
