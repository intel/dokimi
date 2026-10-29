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
- cost, turns and tokens (``input_tokens``, ``output_tokens``,
  ``cache_read_tokens``, ``cache_write_tokens``) are means over
  finished cells, as the harness reported them.
- **uptake**: for an arm that added a file or an MCP server, the cells whose
  agent used it (``result.json`` ``uptake``, see :mod:`.uptake`) over the
  cells with a trace to count from.
- skill arms add ``skill_invoked`` (cells whose agent used the expected skill,
  or any installed one when the task names none) and, when the task has an
  ``expected_skill``, ``skill_selected_correct`` (cells that used it first; ``skill_expected``
  counts the cells that have one).
  Cells whose trace could not be read, or holds no event and no result (the
  harness did not start), are left out of both (``skill_measured`` counts the rest).

A warning is printed when the trace says a model other than the requested one
answered a cell (``model_resolved``): a harness alias can pin an older model.

A row's ``process`` holds the mean ``result.json`` ``process`` counts of its
cells (:mod:`.process_counts`), printed on a line of its own under the row.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import math

from .grading import SCORE_BOUND
from .process_counts import process_line, process_means
from .runner import UNSUPPORTED_FILE, slug

_TOKENS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


class CellFileError(ValueError):
    """A batch file that cannot be read as the JSON object ``repo run`` wrote."""


def _read_object(path: Path) -> dict[str, Any]:
    """The JSON object in ``path``, or a :class:`CellFileError` naming the file."""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CellFileError(f"cannot read {path}: {exc}") from exc
    except (ValueError, RecursionError) as exc:  # JSONDecodeError, bad UTF-8, deep nesting
        raise CellFileError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(rec, dict):
        raise CellFileError(f"{path} must hold a JSON object")
    return rec


def _planned_cells(plan_path: Path) -> set[str]:
    """The cell names in ``plan.json``, or a :class:`CellFileError` when it holds no ``cells`` list of names."""
    cells = _read_object(plan_path).get("cells")
    if not isinstance(cells, list) or not all(isinstance(c, str) for c in cells):
        raise CellFileError(f"{plan_path} must hold a `cells` list of cell names")
    return set(cells)


_AGENT_KEYS = ("timed_out", "is_error", "over_budget", "cost_usd", "num_turns")
_AGENT_NUMBERS = ("cost_usd", "num_turns", *_TOKENS)


# keeps per-task deltas squared (variance in eval/stats.py mean_ci) and sums finite
_SAFE_MAGNITUDE = SCORE_BOUND


def _optional_number(value: object) -> bool:
    """True for None or a finite, non-bool number small enough that its squared deltas and sums stay finite."""
    if value is None:
        return True
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value)) and abs(float(value)) <= _SAFE_MAGNITUDE
    except OverflowError:
        return False


def _skills_usable(skills: object) -> bool:
    """True when ``skills`` is absent, or a mapping holding the keys :func:`summarize` reads."""
    if skills is None:
        return True
    if not isinstance(skills, dict):
        return False
    if not skills or not skills.get("measured", True):
        return True
    needed = ["skill_invoked"] + (["skill_selected_correct"] if skills.get("expected_skill") else [])
    return all(k in skills for k in needed)


def _grade_usable(rec: dict[str, Any]) -> bool:
    """True when ``grade`` holds the keys :func:`summarize` reads for the cell's kind."""
    grade = rec.get("grade")
    if not isinstance(grade, dict) or any(k not in grade for k in ("status", "success")):
        return False
    if "score_task" in rec:
        return "score" in grade
    if "score_only" not in grade:
        return False
    return "score" in grade or not grade["score_only"]


def _check_cell(path: Path, rec: dict[str, Any]) -> None:
    """Raise a :class:`CellFileError` unless ``rec`` has the fields the report and compare read."""
    if not _grade_usable(rec) or not _optional_number(rec["grade"].get("score")):
        raise CellFileError(f"{path} has no usable `grade`")
    agent = rec.get("agent")
    if not isinstance(agent, dict) or any(k not in agent for k in _AGENT_KEYS):
        raise CellFileError(f"{path} has no usable `agent`")
    if not all(_optional_number(agent.get(k)) for k in _AGENT_NUMBERS):
        raise CellFileError(f"{path} has no usable `agent`")
    if not _optional_number(rec.get("score_max")):
        raise CellFileError(f"{path} has no usable `score_max`")
    if not _skills_usable(rec.get("skills")):
        raise CellFileError(f"{path} has no usable `skills`")
    if "score_task" in rec and not isinstance(rec["score_task"], bool):
        raise CellFileError(f"{path} has no usable `score_task`")
    for key in ("task", "arm", "model"):
        if not isinstance(rec.get(key), str):
            raise CellFileError(f"{path} has no usable `{key}`")


def load_cells(batch_dir: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Finished cells (their ``result.json``) and the names of missing ones."""
    plan_path = batch_dir / "_eval" / "plan.json"
    planned = _planned_cells(plan_path) if plan_path.is_file() else set()
    started = {p.parent.name for p in batch_dir.glob("*/meta.json")}
    finished = []
    for path in sorted(batch_dir.glob("*/result.json")):
        rec = _read_object(path)
        _check_cell(path, rec)
        rec["cell"] = path.parent.name
        # The row's kind is the task's, not what its cells' grades say. The runner records it from the
        # eval dir's task.json; the cell's copy is in the run dir, which the grader could rewrite.
        rec["score_task"] = bool(rec["score_task"] if "score_task" in rec else rec["grade"]["score_only"])
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
        rec = _read_object(path)
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
                               "process": process_means(recs),
                               **{k: _mean([r["agent"].get(k) for r in recs]) for k in _TOKENS},
                               "networks": sorted({str(r["agent"].get("network")) for r in recs}),
                               "model_resolved": sorted({m for r in recs for m in resolved_models(r)})}
        uptake = uptake_counts(recs)
        if uptake is not None:
            row["uptake"] = uptake
        if score_only:
            scores = [0.0 if r["grade"]["status"] == "grader_failure" else r["grade"]["score"] for r in recs]
            row.update(kind="score", score=_mean([0.0 if s is None else s for s in scores]))
        else:
            wins = sum(bool(r["grade"]["success"]) for r in recs)
            row.update(kind="gate", successes=wins, success_rate=round(wins / n, 4), ci95=wilson(wins, n))
        used = [r["skills"] for r in recs if r.get("skills") and r["skills"].get("measured", True)]
        if used:  # cells whose trace was unreadable are not counted: no rate from a missing measurement
            row["skill_measured"] = len(used)
            row["skill_invoked"] = sum(bool(u["skill_invoked"]) for u in used)
            expecting = [u for u in used if u.get("expected_skill")]  # a task naming no skill has nothing to select
            if expecting:
                row["skill_expected"] = len(expecting)
                row["skill_selected_correct"] = sum(bool(u["skill_selected_correct"]) for u in expecting)
        rows.append(row)
    return {"batch": str(batch_dir), "cells": len(cells), "missing": missing, "rows": rows,
            "unsupported": load_unsupported(batch_dir),
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
        notes += [f"{r[k]}/{r[d]} {k.replace('_', ' ')}"
                  for k, d in (("skill_invoked", "skill_measured"), ("skill_selected_correct", "skill_expected"))
                  if k in r]
        if "uptake" in r:
            notes.append(f"arm's additions used in {r['uptake']['used']}/{r['uptake']['cells']} cell(s)"
                         + unmeasured_note(r["uptake"]))
        if notes:
            lines.append(" " * (width + 2) + "  " + ", ".join(notes))
        lines += process_line(r.get("process"), width)
    if summary["missing"]:
        shown = summary["missing"][:10]
        lines.append("missing (no result.json; rerun the batch): " + ", ".join(shown)
                     + (f" and {len(summary['missing']) - len(shown)} more" if len(summary["missing"]) > 10 else ""))
    lines += _unsupported_lines(summary.get("unsupported", []))
    if summary["unproxied_network"]:
        lines.append("warning: agents ran with network " + ", ".join(summary["unproxied_network"])
                     + " instead of the egress proxy (not implemented); their commands could reach any host")
    lines += [f"warning: {w}" for w in mismatch_warnings(summary["model_mismatch"])]
    return "\n".join(lines)


def _unsupported_lines(cells: list[dict[str, Any]]) -> list[str]:
    by_task: dict[tuple[str, str], int] = defaultdict(int)
    for u in cells:
        by_task[(str(u["task"]), str(u["reason"]))] += 1
    return [f"unsupported (not run, not failed): {task}, {n} cell(s): {reason}"
            for (task, reason), n in sorted(by_task.items())]


def unmeasured_note(u: dict[str, int]) -> str:
    """What to append to a ``used/cells`` count when some cells left no trace to measure."""
    return f" (warning: {u['unmeasured']} more cell(s) left no trace to measure)" if u.get("unmeasured") else ""


def uptake_counts(recs: list[dict[str, Any]]) -> dict[str, int] | None:
    """``cells`` with a measured ``uptake`` and how many of them ``used`` what the arm added; ``None`` when no
    cell's arm added anything to use."""
    found = [r["uptake"] for r in recs if isinstance(r.get("uptake"), dict)]
    if not found:
        return None
    measured = [u for u in found if u.get("measured")]
    return {"cells": len(measured), "used": sum(bool(u.get("used")) for u in measured),
            "unmeasured": len(found) - len(measured)}


def resolved_models(rec: dict[str, Any]) -> list[str]:
    """The models a cell's trace says answered it (``model_resolved``); empty when the trace could not tell."""
    found = rec.get("model_resolved")
    return [found] if isinstance(found, str) else [str(m) for m in found] if isinstance(found, list) else []


def _normalize_model_id(model_id: str) -> str:
    """Strip Bedrock's revision suffix: any ``-vN:M`` (``-v1:0``, ``-v2:0``) counts as the same model.
    A bare ``-v2`` is part of the id: deepseek-v2 is not v3."""
    return re.sub(r"-v\d+:\d+$", "", model_id)


# Bedrock cross-region inference profile prefixes: the same model served from another region.
_REGION_PREFIXES = ("us-gov.", "global.", "apac.", "us.", "eu.", "jp.", "au.", "ca.")


def _strip_region(model_id: str) -> str:
    for prefix in _REGION_PREFIXES:
        if model_id.startswith(prefix):
            return model_id[len(prefix):]
    return model_id


def _model_prefix_matches(base: str, candidate: str) -> bool:
    """Check if candidate is base optionally preceded by provider prefix (., /)."""
    # base could be the suffix of candidate, with optional prefix
    if base == candidate:
        return True
    # Check if candidate ends with . + base or / + base
    return candidate.endswith(("." + base, "/" + base))


def model_matches(requested: str, resolved: str) -> bool:
    """Whether ``resolved`` is the ``requested`` model: the same id, that id pinned to a snapshot
    date, or either behind a provider prefix (Bedrock's ``us.anthropic.claude-x`` answers as
    ``claude-x``, with any ``-vN:M`` revision too)."""
    # Vertex's @20250929 is the same snapshot as -20250929; an undated side matches any snapshot of it
    req, res = (re.sub(r"@(?=\d{8}$)", "-", _strip_region(_normalize_model_id(m))) for m in (requested, resolved))
    pairs = [(req, res), (re.sub(r"-\d{8}$", "", req), res), (req, re.sub(r"-\d{8}$", "", res))]
    return any(_model_prefix_matches(a, b) or _model_prefix_matches(b, a) for a, b in pairs)


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
