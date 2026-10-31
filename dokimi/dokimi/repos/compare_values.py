# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The per-task values ``repo compare`` pairs: one value per measure for each (model, task) of one arm.

Split out of :mod:`.compare` to keep that module under its line cap. The values are the cells' means, medians
and rates, the process counts and the first-useful measures (:mod:`.useful`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from dokimi.context.consistency import Run, order_distance, pass_hat

from .process_counts import as_measure, process_lists
from .report import reading_cells
from .useful import useful_task_values


def finite_values(values: list[Any]) -> list[float]:
    return [num for v in values if (num := as_measure(v)) is not None]


def task_values(
    cells: list[dict[str, Any]],
    metrics: tuple[str, ...],
    pass_k: int | None = None,
    path_stability: bool = False,
) -> dict[tuple[str, str], dict[str, Any]]:
    """One value per measure for each (model, task) of one arm."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in cells:
        groups[(rec["model"], rec["task"])].append(rec)
    out = {}
    for key, recs in groups.items():
        with reading_cells([f"{r['batch']}/{r['cell']}/result.json" for r in recs]):
            score_task = any(r["score_task"] for r in recs)
            found = {
                "cost_usd": [r["agent"].get("cost_usd") for r in recs],
                "turns": [r["agent"].get("num_turns") for r in recs],
                "first_prompt_tokens": [r["agent"].get("first_prompt_tokens") for r in recs],
                **{f"metric:{name}": [r["metrics"].get(name) for r in recs] for name in metrics},
            }
            found.update({**process_lists(recs), **useful_task_values(recs)})
            if score_task:
                found["score"] = [
                    0.0
                    if r["grade"]["status"] == "grader_failure"
                    else (r["grade"]["score"] or 0.0)
                    for r in recs
                ]
            else:
                found["gate"] = [float(bool(r["grade"]["success"])) for r in recs]
            # unmeasured: a grader failure, or an agent outage (see _agent_outage); either way its 0 is not a measurement
            grader_failed = any(
                r["grade"]["status"] == "grader_failure" or _agent_outage(r) for r in recs
            )
        nums = {measure: finite_values(vals) for measure, vals in found.items()}
        score_max_vals = [r.get("score_max") for r in recs]
        maxes = set(finite_values(score_max_vals))  # cells of two eval trees may differ
        # If any cell has unknown max, or maxes differ, top is None; otherwise take the single value
        has_unknown_max = any(v is None for v in score_max_vals)
        top = (
            (maxes.pop() if len(maxes) == 1 and not has_unknown_max else None)
            if score_task
            else 1.0
        )
        n = {measure: len(v) for measure, v in nums.items()}
        values = {measure: (sum(v) / len(v) if v else None) for measure, v in nums.items()}
        if pass_k and not score_task:
            gates = nums["gate"]
            values[f"pass^{pass_k}"] = (
                pass_hat(len(gates), int(sum(gates)), pass_k) if len(gates) >= pass_k else None
            )
            n[f"pass^{pass_k}"] = len(gates)
        if path_stability:
            runs: list[Run] = [r["path"] for r in recs if r.get("path") is not None]
            values["path_distance"] = order_distance(runs)
            n["path_distance"] = len(runs)
        out[key] = {
            "kind": "score" if score_task else "gate",
            "grader_failed": grader_failed,
            "top": top,
            "n": n,
            "values": values,
        }
    return out


def _agent_outage(rec: dict[str, Any]) -> bool:
    """A cell whose agent never ran: an error result with no turn count or at most one turn, or a harness that
    exited non-zero before it reported any turn (no result record, so no ``is_error``) and was not killed for
    its timeout: an agent killed at its timeout did attempt the task."""
    agent = rec.get("agent") or {}
    turns = finite_values(
        [agent.get("num_turns")]
    )  # a count that is not a number is no count, as in the turns row
    if agent.get("is_error") and (not turns or turns[0] <= 1):
        return True
    rc = agent.get("rc")
    crashed = isinstance(rc, int) and not isinstance(rc, bool) and rc != 0
    return crashed and not turns and not agent.get("timed_out")
