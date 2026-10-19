# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo report``: one row per (task, arm, model) over the cells of a batch directory.

- **success**: cells whose grade passed the task's gate, over every finished
  cell. A grader failure counts as a failure (contract §4.2) and is also
  counted on its own. Score-only tasks report the mean of ``score.metric``
  instead, a grader failure counting as 0.
- **missing**: cells planned or started that have no ``result.json``. They
  never finished, so they are left out of the rate rather than counted as
  failures; rerun the batch to fill them.
- **unsupported**: cells not run because the host did not meet their task's
  ``host`` requirements (``unsupported.json``, with the reason). They are
  neither failures nor missing.
- cost and turns are means over finished cells, as the harness reported them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import math

from .runner import UNSUPPORTED_FILE, slug


def load_cells(batch_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Finished cells (their ``result.json``) and the names of missing ones."""
    plan_path = batch_dir / "_eval" / "plan.json"
    planned = set(json.loads(plan_path.read_text(encoding="utf-8"))["cells"]) if plan_path.is_file() else set()
    started = {p.parent.name for p in batch_dir.glob("*/meta.json")}
    finished = []
    for path in sorted(batch_dir.glob("*/result.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        rec["cell"] = path.parent.name
        # The row's kind is the task's, not what its cells' grades say. The runner records it from the
        # eval dir's task.json; the cell's copy is in the run dir, which the grader could rewrite.
        rec["score_task"] = bool(rec.get("score_task", rec["grade"]["score_only"]))
        finished.append(rec)
    done = {r["cell"] for r in finished}
    unsupported = {p.parent.name for p in batch_dir.glob(f"*/{UNSUPPORTED_FILE}")}
    return finished, sorted((planned | started) - done - unsupported)


def load_unsupported(batch_dir: Path) -> list[dict[str, Any]]:
    """Cells the runner did not run on a host that missed their task's ``host`` requirements."""
    out = []
    for path in sorted(batch_dir.glob(f"*/{UNSUPPORTED_FILE}")):
        if (path.parent / "result.json").is_file():
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        out.append({"cell": path.parent.name, "task": rec.get("task"), "arm": rec.get("arm"),
                    "model": rec.get("model"), "reason": rec.get("reason")})
    return out


def summarize(batch_dir: Path) -> dict[str, Any]:
    cells, missing = load_cells(batch_dir)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in cells:
        groups[(rec["task"], rec["arm"], rec["model"])].append(rec)
    missing_by: dict[tuple[str, str, str], int] = defaultdict(int)
    for name in missing:
        parts = name.split("__")
        if len(parts) == 4:
            missing_by[(parts[0], parts[1], parts[2])] += 1
    rows = []
    for key in sorted(groups):
        recs = groups[key]
        n = len(recs)
        failures = sum(r["grade"]["status"] == "grader_failure" for r in recs)
        score_only = any(r["score_task"] for r in recs)
        row: dict[str, Any] = {"task": key[0], "arm": key[1], "model": key[2], "n": n,
                               "grader_failures": failures,
                               "agent_timeouts": sum(bool(r["agent"]["timed_out"]) for r in recs),
                               "agent_errors": sum(bool(r["agent"]["is_error"]) for r in recs),
                               "over_budget": sum(bool(r["agent"]["over_budget"]) for r in recs),
                               "cost_unknown": sum(r["agent"]["cost_usd"] is None for r in recs),
                               "missing": missing_by.get((key[0], _cell_arm(key[1]), _cell_model(key[2])), 0),
                               "cost_usd": _mean([r["agent"]["cost_usd"] for r in recs]),
                               "total_cost_usd": round(sum(r["agent"]["cost_usd"] or 0 for r in recs), 6),
                               "turns": _mean([r["agent"]["num_turns"] for r in recs]),
                               "networks": sorted({str(r["agent"].get("network")) for r in recs})}
        if score_only:
            scores = [0.0 if r["grade"]["status"] == "grader_failure" else r["grade"]["score"] for r in recs]
            row.update(kind="score", score=_mean([0.0 if s is None else s for s in scores]))
        else:
            wins = sum(bool(r["grade"]["success"]) for r in recs)
            row.update(kind="gate", successes=wins, success_rate=round(wins / n, 4), ci95=wilson(wins, n))
        rows.append(row)
    return {"batch": str(batch_dir), "cells": len(cells), "missing": missing, "rows": rows,
            "unsupported": load_unsupported(batch_dir),
            "total_cost_usd": round(sum(r["agent"]["cost_usd"] or 0 for r in cells), 6),
            "unproxied_network": sorted({str(r["agent"].get("network")) for r in cells} - {"none"})}


def format_report(summary: dict[str, Any]) -> str:
    rows = summary["rows"]
    width = max([len(r["task"]) for r in rows] + [4])
    arm_w = max([len(r["arm"]) for r in rows] + [3])
    model_w = max([len(r["model"]) for r in rows] + [5])
    head = (f"{'task':<{width}}  {'arm':<{arm_w}}  {'model':<{model_w}}  {'n':>3}  {'result':<22}  "
            f"{'grader_fail':>11}  {'missing':>7}  {'cost':>8}  {'turns':>6}")
    lines = [f"{summary['batch']}: {summary['cells']} finished cells, {len(summary['missing'])} missing, "
             f"${summary['total_cost_usd']:.4f}", head]
    for r in rows:
        if r["kind"] == "gate":
            lo, hi = r["ci95"]
            result = f"{r['successes']}/{r['n']} pass [{lo:.2f}, {hi:.2f}]"
        else:
            result = "score -" if r["score"] is None else f"score {r['score']:.2f}"
        cost = "-" if r["cost_usd"] is None else f"{r['cost_usd']:.4f}"
        turns = "-" if r["turns"] is None else f"{r['turns']:.1f}"
        lines.append(f"{r['task']:<{width}}  {r['arm']:<{arm_w}}  {r['model']:<{model_w}}  {r['n']:>3}  "
                     f"{result:<22}  {r['grader_failures']:>11}  {r['missing']:>7}  {cost:>8}  {turns:>6}")
        noted = ("agent_timeouts", "agent_errors", "over_budget", "cost_unknown")
        notes = [f"{r[k]} {k.replace('_', ' ')}" for k in noted if r[k]]
        if notes:
            lines.append(" " * (width + 2) + "  " + ", ".join(notes))
    if summary["missing"]:
        shown = summary["missing"][:10]
        lines.append("missing (no result.json; rerun the batch): " + ", ".join(shown)
                     + (f" and {len(summary['missing']) - len(shown)} more" if len(summary["missing"]) > 10 else ""))
    lines += _unsupported_lines(summary.get("unsupported", []))
    if summary["unproxied_network"]:
        lines.append("warning: agents ran with network " + ", ".join(summary["unproxied_network"])
                     + " instead of the egress proxy (not implemented); their commands could reach any host")
    return "\n".join(lines)


def _unsupported_lines(cells: list[dict[str, Any]]) -> list[str]:
    by_task: dict[tuple[str, str], int] = defaultdict(int)
    for u in cells:
        by_task[(str(u["task"]), str(u["reason"]))] += 1
    return [f"unsupported (not run, not failed): {task}, {n} cell(s): {reason}"
            for (task, reason), n in sorted(by_task.items())]


def _cell_arm(arm: str) -> str:
    return slug(arm.replace(":", "-"))


def _cell_model(model: str) -> str:
    return slug(model)


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score 95% interval for a success rate; sound at the small n of a batch, unlike p +- 2se."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def _mean(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(nums) / len(nums), 4) if nums else None
