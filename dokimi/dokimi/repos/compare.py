# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo compare``: treatment arms against a baseline arm over the cells of one or more batches.

The unit is the task, not the cell. Repetitions of one task share its
difficulty, and rep 1 of one arm has nothing to do with rep 1 of another, so
cells are first reduced to one value per (task, arm, model):

- **gate**: the share of cells that passed the task's gate (a grader failure
  counts as a failure, as in ``repo report``);
- **score**: the mean of ``score.metric`` for score-only tasks (a grader
  failure counts as 0);
- **cost_usd**, **turns**: the per-cell means the harness reported;
- **metric:<name>**: the mean of ``grade.json`` ``metrics.<name>`` over the
  cells that report it (``true`` is 1), for ``--metric``.

A task enters a comparison only when both arms have a value for it; the others
are listed as unpaired. Per measure the comparison gives the mean per-task
delta (treatment minus baseline) with a percentile bootstrap interval over
tasks, the tasks that went up, down or stayed, and a two-sided sign-flip
permutation p on the per-task deltas: exact up to :data:`EXACT_MAX` non-zero
deltas, Monte Carlo above. At one cell per task and arm the deltas are -1, 0
or +1 and the exact p equals McNemar's exact test. Holm's correction runs over
the gate and score comparisons of one invocation; cost, turns and metrics are
descriptive and keep their raw p.
"""

from __future__ import annotations

import itertools
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from dokimi.eval.stats import adjust_pvalues, bootstrap_ci

from .report import load_cells

#: Largest number of non-zero deltas whose sign-flip distribution is enumerated (2**16 patterns).
EXACT_MAX = 16
#: Resamples of the Monte Carlo sign-flip test above :data:`EXACT_MAX`.
N_RESAMPLES = 20_000
#: Deltas closer to zero than this are ties.
EPS = 1e-9
#: Measures Holm's correction runs over.
PRIMARY = ("gate", "score")


class CompareError(ValueError):
    """The arms or batches named cannot be compared."""


def parse_selector(text: str) -> tuple[str | None, str]:
    """``ARM`` or ``BATCH/ARM`` (batch = the batch directory's name) as ``(batch, arm)``."""
    batch, sep, arm = text.rpartition("/")
    if not arm or (sep and not batch):
        raise CompareError(f"bad arm selector {text!r}: expected ARM or BATCH/ARM")
    return (batch or None), arm


def selector_matches(selector: tuple[str | None, str], cell: dict[str, Any]) -> bool:
    batch, arm = selector
    return cell["arm"] == arm and (batch is None or cell["batch"] == batch)


def load_batches(batch_dirs: list[Path], metrics: tuple[str, ...] = ()) -> tuple[list[dict[str, Any]], list[str]]:
    """Finished cells of every batch, each with its ``batch`` and the requested ``grade.json`` metrics."""
    names = [d.resolve().name for d in batch_dirs]  # resolved: Path('.').name is empty
    if len(set(names)) != len(names):
        raise CompareError(f"batch directories must have distinct names: {', '.join(names)}")
    cells, missing = [], []
    for batch_dir, name in zip(batch_dirs, names, strict=True):
        found, gone = load_cells(batch_dir)
        missing += [f"{name}/{cell}" for cell in gone]
        for rec in found:
            rec["batch"] = name
            rec["metrics"] = _grade_metrics(batch_dir / rec["cell"] / "grade.json", metrics) if metrics else {}
            cells.append(rec)
    return cells, missing


def compare(batch_dirs: list[Path], baseline: str, treatments: list[str] | None = None,
            models: list[str] | None = None, metrics: tuple[str, ...] = (), seed: int = 0) -> dict[str, Any]:
    if treatments is not None and not treatments:
        raise CompareError("no treatment arms named; leave --arm out to compare every other arm")
    metrics = tuple(dict.fromkeys(metrics))
    cells, missing = load_batches(batch_dirs, metrics)
    base_sel = parse_selector(baseline)
    if not any(selector_matches(base_sel, c) for c in cells):
        raise CompareError(f"no finished cell matches the baseline {baseline!r}")
    treatments = _treatments(cells, baseline, base_sel, treatments)
    measures = ["gate", "score", "cost_usd", "turns", *(f"metric:{m}" for m in metrics)]
    base_vals = _task_values([c for c in cells if selector_matches(base_sel, c)], metrics)
    rows: list[dict[str, Any]] = []
    unpaired: list[dict[str, Any]] = []
    for text in treatments:
        treat_vals = _task_values([c for c in cells if selector_matches(parse_selector(text), c)], metrics)
        base_models, treat_models = {m for m, _ in base_vals}, {m for m, _ in treat_vals}
        for model in sorted(base_models ^ treat_models):
            if not models or model in models:  # a model only one arm ran has no task to pair: list its tasks
                seen, gone = (base_vals, "treatment") if model in base_models else (treat_vals, "baseline")
                unpaired += [{"treatment": text, "model": model, "task": t, "missing_in": gone}
                             for m, t in sorted(seen) if m == model]
        shared_models = sorted(base_models & treat_models)
        for model in [m for m in shared_models if not models or m in models]:
            _compare_model(text, model, base_vals, treat_vals, measures, seed, rows, unpaired)
    _check_models(models, rows, unpaired)
    _holm(rows)
    return {"batches": [str(d) for d in batch_dirs], "baseline": baseline, "treatments": treatments,
            "cells": len(cells), "missing": missing, "comparisons": rows, "unpaired": unpaired}


def _check_models(models: list[str] | None, rows: list[dict[str, Any]], unpaired: list[dict[str, Any]]) -> None:
    """A ``--model`` that no comparison has is a typo or a model of no shared task: refuse it, not print nothing."""
    unknown = sorted(set(models or ()) - {r["model"] for r in rows} - {u["model"] for u in unpaired})
    if unknown:
        raise CompareError(f"no compared cells for --model {', '.join(unknown)}")


def _holm(rows: list[dict[str, Any]]) -> None:
    """``p_holm`` on the gate and score rows, corrected as one family."""
    primary = [r for r in rows if r["measure"] in PRIMARY]
    for row, p in zip(primary, adjust_pvalues([r["p"] for r in primary]), strict=True):
        row["p_holm"] = p


def _compare_model(treatment: str, model: str, base_vals: dict[tuple[str, str], dict[str, Any]],
                   treat_vals: dict[tuple[str, str], dict[str, Any]], measures: list[str], seed: int,
                   rows: list[dict[str, Any]], unpaired: list[dict[str, Any]]) -> None:
    """Append one row per measure of one (treatment, model), and the tasks only one arm has."""
    base_tasks = {t for m, t in base_vals if m == model}
    treat_tasks = {t for m, t in treat_vals if m == model}
    for task in sorted(base_tasks ^ treat_tasks):
        unpaired.append({"treatment": treatment, "model": model, "task": task,
                         "missing_in": "treatment" if task in base_tasks else "baseline"})
    shared = sorted(base_tasks & treat_tasks)
    for measure in measures:
        found = (_pair(task, base_vals[(model, task)], treat_vals[(model, task)], measure) for task in shared)
        pairs = [p for p in found if p is not None]
        if pairs:
            rows.append({"treatment": treatment, "model": model, "measure": measure,
                         **paired_summary(pairs, seed=seed)})


def _treatments(cells: list[dict[str, Any]], baseline: str, base_sel: tuple[str | None, str],
                treatments: list[str] | None) -> list[str]:
    """The treatment selectors to compare, checked: each matches cells, and none of the baseline's."""
    if treatments is None:
        # every other arm that has cells; the baseline's own arm from another batch is named by its batch
        treatments = sorted({f"{c['batch']}/{c['arm']}" if c["arm"] == base_sel[1] else c["arm"]
                             for c in cells if not selector_matches(base_sel, c)})
    for text in treatments:
        sel = parse_selector(text)
        if sel == base_sel:
            raise CompareError(f"treatment {text!r} is the baseline")
        matched = [c for c in cells if selector_matches(sel, c)]
        if not matched:
            raise CompareError(f"no finished cell matches the treatment {text!r}")
        if any(selector_matches(base_sel, c) for c in matched):
            raise CompareError(f"treatment {text!r} also matches cells of the baseline {baseline!r}; "
                               "name its batch (BATCH/ARM)")
    return list(dict.fromkeys(treatments))  # a repeated arm would double its rows and its share of Holm's family


def _pair(task: str, base: dict[str, Any], treat: dict[str, Any], measure: str) -> dict[str, Any] | None:
    """One task's values of ``measure`` in both arms, or ``None`` when either arm has none.

    gate and score apply only to tasks of that kind in both arms; arms that disagree on the kind (batches of
    different eval trees) leave the task out of both.
    """
    if measure in PRIMARY and not base["kind"] == treat["kind"] == measure:
        return None
    bv, tv = base["values"].get(measure), treat["values"].get(measure)
    if bv is None or tv is None:
        return None
    return {"task": task, "baseline": bv, "treatment": tv, "delta": tv - bv,
            "n_baseline": base["n"], "n_treatment": treat["n"]}


def paired_summary(pairs: list[dict[str, Any]], seed: int = 0) -> dict[str, Any]:
    """Statistics over per-task pairs ``{task, baseline, treatment, delta, ...}``.

    The values are taken at full precision and rounded only in the output: rounded rates such as 1/3 and 2/3
    no longer sum to the same totals, which would move sign patterns across the observed one in the
    permutation test.
    """
    deltas = [p["delta"] for p in pairs]
    ci = bootstrap_ci(deltas, seed=seed)
    return {
        "n_tasks": len(pairs),
        "baseline_mean": _round(_mean([p["baseline"] for p in pairs])),
        "treatment_mean": _round(_mean([p["treatment"] for p in pairs])),
        "delta": {"mean": ci["mean"], "lo": ci["lo"], "hi": ci["hi"]},
        "p": sign_flip_p(deltas, seed=seed),
        "p_holm": None,
        "up": sum(d > EPS for d in deltas),
        "down": sum(d < -EPS for d in deltas),
        "tied": sum(abs(d) <= EPS for d in deltas),
        "tasks": [{**p, "baseline": _round(p["baseline"]), "treatment": _round(p["treatment"]),
                   "delta": _round(p["delta"])} for p in pairs],
    }


def sign_flip_p(deltas: list[float], seed: int = 0, n_resamples: int = N_RESAMPLES) -> float | None:
    """Two-sided p of ``|sum(deltas)|`` when each delta's sign is a fair coin under the null.

    ``None`` when every delta is a tie: there is no evidence either way, and 1.0 would read as a measured null.
    """
    d = [x for x in deltas if abs(x) > EPS]
    if not d:
        return None
    observed = abs(sum(d)) - EPS
    if len(d) <= EXACT_MAX:
        hits = sum(abs(sum(s * x for s, x in zip(signs, d, strict=True))) >= observed
                   for signs in itertools.product((1, -1), repeat=len(d)))
        return round(hits / 2 ** len(d), 6)
    rng = random.Random(seed)
    hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in d)) >= observed for _ in range(n_resamples))
    return round((hits + 1) / (n_resamples + 1), 6)


def format_compare(summary: dict[str, Any]) -> str:
    lines = [f"baseline {summary['baseline']}: {summary['cells']} finished cells in "
             f"{len(summary['batches'])} batch(es), {len(summary['missing'])} missing"]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in summary["comparisons"]:
        groups[(row["treatment"], row["model"])].append(row)
    width = max([len(r["measure"]) for r in summary["comparisons"]] + [7])
    for (treatment, model), rows in groups.items():
        lines += ["", f"{treatment} vs {summary['baseline']}, model {model}",
                  f"  {'measure':<{width}}  {'tasks':>5}  {'baseline':>9}  {'treatment':>9}  "
                  f"{'delta [95% CI]':<26}  {'p':>6}  {'p_holm':>6}  up/down/tied"]
        for r in rows:
            d = r["delta"]
            delta = f"{d['mean']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]"
            lines.append(f"  {r['measure']:<{width}}  {r['n_tasks']:>5}  {_num(r['baseline_mean']):>9}  "
                         f"{_num(r['treatment_mean']):>9}  {delta:<26}  {_p(r['p']):>6}  {_p(r['p_holm']):>6}  "
                         f"{r['up']}/{r['down']}/{r['tied']}")
        for r in rows:
            if r["measure"] in PRIMARY:
                moved = [t for t in r["tasks"] if abs(t["delta"]) > EPS]
                if moved:
                    lines.append(f"  {r['measure']} changed: " + ", ".join(
                        f"{t['task']} {t['delta']:+.2f}" for t in sorted(moved, key=lambda t: t["delta"])))
    if summary["unpaired"]:
        shown = summary["unpaired"][:10]
        lines.append("unpaired (no cells in one arm): " + ", ".join(
            f"{u['task']} ({u['model']}, none in {u['missing_in']})" for u in shown)
            + (f" and {len(summary['unpaired']) - len(shown)} more" if len(summary["unpaired"]) > 10 else ""))
    if summary["missing"]:
        lines.append(f"{len(summary['missing'])} cells missing (no result.json): `repo report` lists them")
    return "\n".join(lines)


def _task_values(cells: list[dict[str, Any]], metrics: tuple[str, ...]) -> dict[tuple[str, str], dict[str, Any]]:
    """One value per measure for each (model, task) of one arm."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in cells:
        groups[(rec["model"], rec["task"])].append(rec)
    out = {}
    for key, recs in groups.items():
        score_task = any(r["score_task"] for r in recs)
        values: dict[str, float | None] = {
            "cost_usd": _mean([r["agent"].get("cost_usd") for r in recs]),
            "turns": _mean([r["agent"].get("num_turns") for r in recs]),
        }
        if score_task:
            values["score"] = _mean([0.0 if r["grade"]["status"] == "grader_failure" else (r["grade"]["score"] or 0.0)
                                     for r in recs])
        else:
            values["gate"] = sum(bool(r["grade"]["success"]) for r in recs) / len(recs)
        for name in metrics:
            values[f"metric:{name}"] = _mean([r["metrics"].get(name) for r in recs])
        out[key] = {"kind": "score" if score_task else "gate", "n": len(recs), "values": values}
    return out


def _grade_metrics(path: Path, names: tuple[str, ...]) -> dict[str, float]:
    """The requested numeric or boolean ``metrics`` of a cell's ``grade.json``; absent ones are left out."""
    try:
        grade = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    found = grade.get("metrics") if isinstance(grade, dict) else None
    if not isinstance(found, dict):
        return {}
    out = {}
    for name in names:
        value = found.get(name)
        if isinstance(value, bool):
            out[name] = 1.0 if value else 0.0
        elif isinstance(value, (int, float)) and math.isfinite(value):  # NaN and inf are not measurements
            out[name] = float(value)
    return out


def _mean(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    return sum(nums) / len(nums) if nums else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _p(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"
