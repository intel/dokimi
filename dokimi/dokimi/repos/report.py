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
- cost and turns are means over finished cells, as the harness reported them.

A warning is printed when the trace says a model other than the requested one
answered a cell (``model_resolved``): a harness alias can pin an older model.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import math

from .runner import slug


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
    return finished, sorted((planned | started) - done)


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
                               "networks": sorted({str(r["agent"].get("network")) for r in recs}),
                               "model_resolved": sorted({m for r in recs for m in resolved_models(r)})}
        if score_only:
            scores = [0.0 if r["grade"]["status"] == "grader_failure" else r["grade"]["score"] for r in recs]
            row.update(kind="score", score=_mean([0.0 if s is None else s for s in scores]))
        else:
            wins = sum(bool(r["grade"]["success"]) for r in recs)
            row.update(kind="gate", successes=wins, success_rate=round(wins / n, 4), ci95=wilson(wins, n))
        rows.append(row)
    return {"batch": str(batch_dir), "cells": len(cells), "missing": missing, "rows": rows,
            "total_cost_usd": round(sum(r["agent"]["cost_usd"] or 0 for r in cells), 6),
            "unproxied_network": sorted({str(r["agent"].get("network")) for r in cells} - {"none"}),
            "model_mismatch": model_mismatch(cells)}


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
    if summary["unproxied_network"]:
        lines.append("warning: agents ran with network " + ", ".join(summary["unproxied_network"])
                     + " instead of the egress proxy (not implemented); their commands could reach any host")
    lines += [f"warning: {w}" for w in mismatch_warnings(summary["model_mismatch"])]
    return "\n".join(lines)


def resolved_models(rec: dict[str, Any]) -> list[str]:
    """The models a cell's trace says answered it (``model_resolved``); empty when the trace could not tell."""
    found = rec.get("model_resolved")
    return [found] if isinstance(found, str) else [str(m) for m in found] if isinstance(found, list) else []


def _normalize_model_id(model_id: str) -> str:
    """Normalize model ID by stripping trailing version suffix (e.g., -v1:0)."""
    # Strip Bedrock version suffix like -v1 or -v1:0
    return re.sub(r"-v\d+(?::\d+)?$", "", model_id)


def _model_prefix_matches(base: str, candidate: str) -> bool:
    """Check if candidate is base optionally preceded by provider prefix (., /)."""
    # base could be the suffix of candidate, with optional prefix
    if base == candidate:
        return True
    # Check if candidate ends with . + base or / + base
    return candidate.endswith(("." + base, "/" + base))


def model_matches(requested: str, resolved: str) -> bool:
    """Whether ``resolved`` is the ``requested`` model: the same id, or that id behind a provider prefix
    (Bedrock's ``us.anthropic.claude-x`` answers as ``claude-x``, with its ``-v1:0`` too)."""
    # Normalize both by stripping version suffixes, then check prefix equivalence both ways
    req_norm = _normalize_model_id(requested)
    res_norm = _normalize_model_id(resolved)
    return _model_prefix_matches(res_norm, req_norm) or _model_prefix_matches(req_norm, res_norm)


def model_mismatch(cells: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Requested model -> the other models that answered its cells."""
    out: dict[str, set[str]] = defaultdict(set)
    for rec in cells:
        out[rec["model"]] |= {m for m in resolved_models(rec) if not model_matches(rec["model"], m)}
    return {k: sorted(v) for k, v in sorted(out.items()) if v}


def mismatch_warnings(mismatch: dict[str, list[str]]) -> list[str]:
    return [f"cells run with model {model} were answered by {', '.join(found)}; check the harness's model aliases"
            for model, found in mismatch.items()]


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
