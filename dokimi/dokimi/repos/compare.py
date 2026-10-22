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
are listed as unpaired, with the reason (no cells in one arm, a task kind that
differs between the arms, a measure one arm lacks). Per measure the comparison
gives the mean per-task delta (treatment minus baseline) with a 95% interval
over tasks (a percentile bootstrap for gate and score, a t interval for the
continuous measures; none for a single task), the tasks that went up, down or
stayed, and a two-sided sign-flip
permutation p on the per-task deltas: exact up to :data:`EXACT_MAX` non-zero
deltas, Monte Carlo above. At one cell per task and arm the deltas are -1, 0
or +1 and the exact p equals McNemar's exact test. Holm's correction runs over
the gate and score comparisons of one invocation; cost, turns and metrics are
descriptive and keep their raw p.

A gate or score comparison also says whether its tasks had room to move (``headroom``), since a zero delta
on a task set nobody can fail, or nobody can pass, is not evidence of no effect. A task's top is 1.0 for
gate and the task's ``score.max`` for score (recorded in ``result.json``; a batch without it has no known
top, so its score rows are never at ceiling). A pair is at the top only when both arms reach it and tie: a
score past ``score.max`` is an effect, not a top. ``ceiling``: both arms at the top on every paired task;
``floor``: both arms at 0 on every paired task; ``near_ceiling``: neither, at least 2 paired tasks, the
baseline at the top on all but at most one, and the treatment nowhere below the baseline, so at most one task
can improve and a gain resting on one task has a sign-flip p of 1. A pair whose baseline and treatment tops
differ is on two scales. A grader failure or an agent outage (an error result with no turn count or at most
one turn, or a non-zero exit with no turn count that is not a timeout) in either arm scores a 0 that says the
measurement failed, not that the task had no room. Any other case has no flag: ``None``. The flag is a note;
p and Holm are unchanged.

Each selector's batches are reported with the stamp ``repo run`` pins a batch
to (``tree``, ``eval_dir`` digest, ``image``) and the harness versions of its
cells. A warning is printed, not an error, when a bare ``ARM`` pools cells of
several batches, when a batch of a comparison has no stamp to compare, or when
the two arms of a comparison ran from different ``eval_dir`` or ``image``
stamps or harness versions, or their cells of one model were answered by
different models (``model_resolved``): the deltas then mix the treatment with
whatever else changed between the batches. A cell answered by another model
than it asked for is warned about as in ``repo report``. An image ``repo run``
tagged from the tree's own revision follows the tree, so it is compared only
against an ``--image`` given explicitly.

A treatment arm that added a file or an MCP server (``result.json``
``uptake``) whose agents used it in none of their cells is warned about too: its
deltas measure the patch's text (a guidance line, say), not the tool.
"""

from __future__ import annotations

import itertools
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from dokimi.eval.stats import adjust_pvalues, bootstrap_ci, mean_ci

from .report import (load_cells, mismatch_warnings, model_matches, model_mismatch, resolved_models, unmeasured_note,
                     uptake_counts)

#: Largest number of non-zero deltas whose sign-flip distribution is enumerated (2**16 patterns).
EXACT_MAX = 16
#: Resamples of the Monte Carlo sign-flip test above :data:`EXACT_MAX`.
N_RESAMPLES = 20_000
#: Deltas closer to zero than this are ties.
EPS = 1e-9
#: Measures Holm's correction runs over.
PRIMARY = ("gate", "score")
#: Batch stamp keys that must agree between the arms of a comparison; ``tree`` is shown but may differ (another
#: revision of the repository is a treatment of its own).
STAMP_KEYS = ("eval_dir", "image", "graders")


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


def batch_stamp(batch_dir: Path) -> dict[str, str]:
    """What ``repo run`` pinned the batch to (``_eval/batch.json``); empty for a batch that has none."""
    try:
        stamp = json.loads((batch_dir / "_eval" / "batch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: str(v) for k, v in stamp.items() if k in ("tree", *STAMP_KEYS)} if isinstance(stamp, dict) else {}


def compare(batch_dirs: list[Path], baseline: str, treatments: list[str] | None = None,
            models: list[str] | None = None, metrics: tuple[str, ...] = (), seed: int = 0) -> dict[str, Any]:
    metrics = tuple(dict.fromkeys(metrics))
    cells, missing = load_batches(batch_dirs, metrics)
    _check_metrics(metrics, cells)
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
        unpaired += _one_arm_models(text, base_vals, treat_vals, models)
        shared_models = sorted({m for m, _ in base_vals} & {m for m, _ in treat_vals})
        for model in [m for m in shared_models if not models or m in models]:
            _compare_model(text, model, base_vals, treat_vals, measures, seed, rows, unpaired)
    _check_models(models, rows, unpaired)
    _holm(rows)
    return {"batches": [str(d) for d in batch_dirs], "baseline": baseline, "treatments": treatments,
            "cells": len(cells), "missing": missing, **_provenance(batch_dirs, cells, baseline, treatments, models),
            "comparisons": rows, "unpaired": unpaired}


def _one_arm_models(treatment: str, base_vals: dict[tuple[str, str], dict[str, Any]],
                    treat_vals: dict[tuple[str, str], dict[str, Any]], models: list[str] | None) -> list[dict[str, Any]]:
    """The tasks of a model only one arm ran: it has no task to pair, so every task is unpaired."""
    base_models, treat_models = {m for m, _ in base_vals}, {m for m, _ in treat_vals}
    out = []
    for model in sorted(base_models ^ treat_models):
        if not models or model in models:
            seen, gone = (base_vals, "treatment") if model in base_models else (treat_vals, "baseline")
            out += [_unpaired(treatment, model, t, None, f"no cells in {gone}") for m, t in sorted(seen) if m == model]
    return out


def _provenance(batch_dirs: list[Path], cells: list[dict[str, Any]], baseline: str,
                treatments: list[str], models: list[str] | None = None) -> dict[str, Any]:
    """``sources`` (each selector's batches), ``stamps`` (each batch's pin and harness versions), ``uptake`` (each
    treatment's cells that used what its arm added), ``warnings``."""
    names = [d.resolve().name for d in batch_dirs]
    stamps = {name: {**batch_stamp(d), "harness_versions": sorted({str(c.get("harness_version"))
                                                                   for c in cells if c["batch"] == name}),
                     "model_resolved": sorted({m for c in cells if c["batch"] == name for m in resolved_models(c)})}
              for d, name in zip(batch_dirs, names, strict=True)}
    sources = {text: sorted({c["batch"] for c in cells if selector_matches(parse_selector(text), c)})
               for text in [baseline, *treatments]}
    uptake = {text: uptake_counts([c for c in cells if selector_matches(parse_selector(text), c)
                                   and (not models or c["model"] in models)]) for text in treatments}
    return {"sources": sources, "stamps": stamps, "uptake": {k: v for k, v in uptake.items() if v is not None},
            "warnings": _provenance_warnings(cells, baseline, treatments, sources, stamps, models)}


def _models_equivalent(base_models: set[str], treat_models: set[str]) -> bool:
    """Whether base and treatment model sets are equivalent under model_matches() logic.

    Two resolved model sets are equivalent if they differ only by provider prefixes or
    version suffixes that model_matches() considers equivalent. For each base model,
    there must be a matching treatment model and the reverse; the set sizes may differ, since one arm can
    hold several spellings of the same model across repetitions.
    """
    if base_models == treat_models:
        return True
    # Check if each base model has an equivalent in treatment models
    # (model_matches is already bidirectional, so one call per pair suffices)
    for base_model in base_models:
        if not any(model_matches(base_model, treat_model) for treat_model in treat_models):
            return False
    # Check the reverse: every treatment model matches some base model
    for treat_model in treat_models:
        if not any(model_matches(treat_model, base_model) for base_model in base_models):
            return False
    return True


def _provenance_warnings(cells: list[dict[str, Any]], baseline: str, treatments: list[str],
                         sources: dict[str, list[str]], stamps: dict[str, dict[str, Any]],
                         models: list[str] | None = None) -> list[str]:
    """Pooled batches and stamp mismatches between the arms of each comparison; model checks only of ``models``."""
    out = [f"{text} pools cells of {len(batches)} batches ({', '.join(batches)}); name one as BATCH/ARM"
           for text, batches in sources.items() if parse_selector(text)[0] is None and len(batches) > 1]
    unstamped = set()
    for text in treatments:
        batches = sorted(set(sources[baseline]) | set(sources[text]))
        for b in batches if len(batches) > 1 else ():  # the arms of one batch share its pin
            if b not in unstamped and not any(k in stamps[b] for k in STAMP_KEYS):
                unstamped.add(b)
                out.append(f"batch {b} has no stamp (_eval/batch.json); its eval_dir, image and graders are not compared")
        for key in STAMP_KEYS:
            pinned = [b for b in batches if key in stamps[b]]
            if key == "image" and all(_built_from_tree(stamps[b]) for b in pinned):
                continue  # each image follows its own tree, and another tree is the treatment
            seen = {stamps[b][key] for b in pinned}
            if len(seen) > 1:
                out.append(f"{text} vs {baseline}: the batches ran from different {key} ({', '.join(sorted(seen))})")
        versions = {str(c.get("harness_version")) for sel in (baseline, text) for c in cells
                    if selector_matches(parse_selector(sel), c)}
        if len(versions) > 1:
            out.append(f"{text} vs {baseline}: cells ran on different harness versions ({', '.join(sorted(versions))})")
        arms = [[c for c in cells if selector_matches(parse_selector(sel), c) and (not models or c["model"] in models)]
                for sel in (baseline, text)]
        for model in sorted({c["model"] for c in arms[0]} & {c["model"] for c in arms[1]}):
            base, treat = ({m for c in arm if c["model"] == model for m in resolved_models(c)} for arm in arms)
            if base and treat and not _models_equivalent(base, treat):
                out.append(f"{text} vs {baseline}: model {model} was answered by {', '.join(sorted(base))} in "
                           f"baseline, {', '.join(sorted(treat))} in treatment")
        uptake = uptake_counts(arms[1])
        if uptake and uptake["cells"] and not uptake["used"]:
            out.append(f"{text}: the agent used none of what the arm added in {uptake['cells']} cell(s); "
                       "a delta measures the patch's text, not the tool")
    compared = [c for c in cells if (not models or c["model"] in models)
                and any(selector_matches(parse_selector(sel), c) for sel in (baseline, *treatments))]
    return out + mismatch_warnings(model_mismatch(compared))


def _built_from_tree(stamp: dict[str, Any]) -> bool:
    """Whether the stamped image is the one ``repo run`` tags from the tree (``dokimi/<id>:<sha12>``, or
    ``:worktree-<digest>`` for a working tree), not one ``--image`` named."""
    repo, _, tag = stamp.get("image", "").rpartition(":")
    revision = stamp.get("tree", "").rpartition("@")[2]
    return repo.startswith("dokimi/") and (tag.startswith("worktree-") or bool(revision) and tag == revision[:12])


def _check_metrics(metrics: tuple[str, ...], cells: list[dict[str, Any]]) -> None:
    """A ``--metric`` that no finished cell reports is a typo or not a number: refuse it, not print nothing."""
    unknown = [m for m in metrics if not any(m in c["metrics"] for c in cells)]
    if unknown:
        raise CompareError(f"no finished cell reports a numeric or boolean --metric {', '.join(unknown)}")


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
        unpaired.append(_unpaired(treatment, model, task, None,
                                  "no cells in " + ("treatment" if task in base_tasks else "baseline")))
    shared = sorted(base_tasks & treat_tasks)
    for task in shared:
        b, t = base_vals[(model, task)]["kind"], treat_vals[(model, task)]["kind"]
        if b != t:  # batches of different eval trees: the primary result of this task has no pair
            unpaired.append(_unpaired(treatment, model, task, None, f"{b} task in baseline, {t} in treatment"))
    for measure in measures:
        pairs = []
        for task in shared:
            base, treat = base_vals[(model, task)], treat_vals[(model, task)]
            if measure in PRIMARY and not base["kind"] == treat["kind"] == measure:
                continue  # a task of the other kind, or one listed above
            pair = _pair(task, base, treat, measure)
            if pair is None:
                lacking = [arm for arm, v in (("baseline", base), ("treatment", treat))
                           if v["values"].get(measure) is None]
                unpaired.append(_unpaired(treatment, model, task, measure,
                                          f"no {measure} in {' or '.join(lacking)}"))
            else:
                pairs.append(pair)
        if pairs:
            flag = None
            if measure in PRIMARY:
                tops = [(base_vals[(model, p["task"])]["top"], treat_vals[(model, p["task"])]["top"]) for p in pairs]
                failed = [base_vals[(model, p["task"])]["grader_failed"]
                          or treat_vals[(model, p["task"])]["grader_failed"] for p in pairs]
                flag = headroom(pairs, tops, failed)
            rows.append({"treatment": treatment, "model": model, "measure": measure,
                         **paired_summary(pairs, measure, seed=seed), "headroom": flag})


def headroom(pairs: list[dict[str, Any]], tops: list[tuple[float | None, float | None]],
             failed: list[bool] | None = None) -> str | None:
    """``ceiling``, ``floor``, ``near_ceiling`` or ``None`` for one gate or score comparison (module docstring).

    ``tops`` holds each pair's (baseline, treatment) top value, ``None`` where it is not known; ``failed``
    marks the pairs with a grader failure or an agent outage in either arm (its 0 is not a measurement).
    """
    if any(b != t for b, t in tops):
        return None  # two scales: neither "at the top" nor the delta compares
    if failed and any(failed):
        return None  # its 0 is not a measurement, at either end (a score.max of 0 would read it as the top)

    def at_top(value: float, top: float | None) -> bool:
        return top is not None and value >= top - EPS

    # score.max is what the oracle must reach, not a bound: a pair that moved past it is an effect, not a top
    base_top = [at_top(p["baseline"], b) and abs(p["treatment"] - p["baseline"]) <= EPS
                for p, (b, _) in zip(pairs, tops, strict=True)]
    if all(base_top) and all(at_top(p["treatment"], t) for p, (_, t) in zip(pairs, tops, strict=True)):
        return "ceiling"
    if all(abs(p["baseline"]) <= EPS and abs(p["treatment"]) <= EPS for p in pairs):
        return "floor"
    if (len(pairs) >= 2 and base_top.count(False) <= 1
            and all(p["treatment"] >= p["baseline"] - EPS for p in pairs)):
        return "near_ceiling"
    return None


def _unpaired(treatment: str, model: str, task: str, measure: str | None, reason: str) -> dict[str, Any]:
    """A task left out of the comparison: of every measure (``measure`` None) or of one."""
    return {"treatment": treatment, "model": model, "task": task, "measure": measure, "reason": reason}


def _treatments(cells: list[dict[str, Any]], baseline: str, base_sel: tuple[str | None, str],
                treatments: list[str] | None) -> list[str]:
    """The treatment selectors to compare, checked: each matches cells, and none of the baseline's."""
    if treatments is not None and not treatments:
        raise CompareError("no treatment arms named; leave --arm out to compare every other arm")
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
    # a repeated arm would double its rows and its share of Holm's family; so would two spellings of one arm
    treatments = list(dict.fromkeys(treatments))
    selected: dict[frozenset[tuple[str, str]], str] = {}
    for text in treatments:
        sel = parse_selector(text)
        key = frozenset((c["batch"], c["cell"]) for c in cells if selector_matches(sel, c))
        if key in selected:
            raise CompareError(f"treatments {selected[key]!r} and {text!r} select the same cells; name one")
        selected[key] = text
    return treatments


def _pair(task: str, base: dict[str, Any], treat: dict[str, Any], measure: str) -> dict[str, Any] | None:
    """One task's values of ``measure`` in both arms, or ``None`` when either arm has none.

    ``n_baseline``/``n_treatment`` count the cells behind each value: for a metric, the cells that report it.
    """
    bv, tv = base["values"].get(measure), treat["values"].get(measure)
    if bv is None or tv is None:
        return None
    return {"task": task, "baseline": bv, "treatment": tv, "delta": tv - bv,
            "n_baseline": base["n"][measure], "n_treatment": treat["n"][measure]}


def paired_summary(pairs: list[dict[str, Any]], measure: str = "gate", seed: int = 0) -> dict[str, Any]:
    """Statistics over per-task pairs ``{task, baseline, treatment, delta, ...}``.

    The values are taken at full precision and rounded only in the output: rounded rates such as 1/3 and 2/3
    no longer sum to the same totals, which would move sign patterns across the observed one in the
    permutation test. The interval is a percentile bootstrap for the bounded gate and score shares and a t
    interval (:func:`~dokimi.eval.stats.mean_ci`) for the continuous measures, where the bootstrap
    under-covers at these task counts; one task has no interval (``lo``/``hi`` None), not a zero-width one.
    """
    deltas = [p["delta"] for p in pairs]
    ci = (bootstrap_ci if measure in PRIMARY else mean_ci)(deltas, seed=seed)
    lo, hi = (float(ci["lo"]), float(ci["hi"])) if len(deltas) > 1 else (None, None)  # mean_ci gives numpy floats
    return {
        "n_tasks": len(pairs),
        "baseline_mean": _round(_mean([p["baseline"] for p in pairs])),
        "treatment_mean": _round(_mean([p["treatment"] for p in pairs])),
        "delta": {"mean": ci["mean"], "lo": lo, "hi": hi},
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
    lines = _format_header(summary)
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
            delta = f"{d['mean']:+.3f} " + ("-" if d["lo"] is None else f"[{d['lo']:+.3f}, {d['hi']:+.3f}]")
            lines.append(f"  {r['measure']:<{width}}  {r['n_tasks']:>5}  {_num(r['baseline_mean']):>9}  "
                         f"{_num(r['treatment_mean']):>9}  {delta:<26}  {_p(r['p']):>6}  {_p(r['p_holm']):>6}  "
                         f"{r['up']}/{r['down']}/{r['tied']}")
        for r in rows:
            if r["measure"] in PRIMARY:
                moved = [t for t in r["tasks"] if abs(t["delta"]) > EPS]
                if moved:
                    lines.append(f"  {r['measure']} changed: " + ", ".join(
                        f"{t['task']} {t['delta']:+.2f}" for t in sorted(moved, key=lambda t: t["delta"])))
                if r["headroom"]:
                    lines.append(f"  {r['measure']} {_headroom_note(r)}")
    if summary["unpaired"]:
        shown = summary["unpaired"][:10]
        lines.append("unpaired: " + ", ".join(
            f"{u['task']} ({u['model']}, {u['reason']})" for u in shown)
            + (f" and {len(summary['unpaired']) - len(shown)} more" if len(summary["unpaired"]) > 10 else ""))
    if summary["missing"]:
        lines.append(f"{len(summary['missing'])} cells missing (no result.json): `repo report` lists them")
    lines += [f"uptake: {text} used what its arm added in {u['used']}/{u['cells']} cell(s){unmeasured_note(u)}"
              for text, u in summary.get("uptake", {}).items()]
    lines += [f"warning: {w}" for w in summary["warnings"]]
    return "\n".join(lines)


def _headroom_note(row: dict[str, Any]) -> str:
    n, gate = row["n_tasks"], row["measure"] == "gate"
    if row["headroom"] == "ceiling":
        return (f"ceiling: both arms {'pass' if gate else 'reach score.max on'} all {n} paired tasks; "
                "no headroom to show an effect")
    if row["headroom"] == "floor":
        return f"floor: both arms {'fail' if gate else 'score 0 on'} all {n} paired tasks; no headroom to show an effect"
    return (f"near ceiling: the baseline {'passes' if gate else 'reaches score.max on'} all but at most one of "
            f"{n} paired tasks; too few can improve to show a gain")


def _format_header(summary: dict[str, Any]) -> list[str]:
    lines = [f"baseline {summary['baseline']}: {summary['cells']} finished cells in "
             f"{len(summary['batches'])} batch(es), {len(summary['missing'])} missing"]
    for text, batches in summary["sources"].items():
        lines.append(f"  {text}: {', '.join(batches)}")
    for name, stamp in summary["stamps"].items():
        shown = [f"{k} {stamp[k][:12] if k in ('eval_dir', 'graders') else stamp[k]}" for k in ("tree", *STAMP_KEYS) if k in stamp]
        lines.append(f"  batch {name}: " + ", ".join([*shown, "harness " + ", ".join(stamp["harness_versions"])]))
    return lines


def _task_values(cells: list[dict[str, Any]], metrics: tuple[str, ...]) -> dict[tuple[str, str], dict[str, Any]]:
    """One value per measure for each (model, task) of one arm."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rec in cells:
        groups[(rec["model"], rec["task"])].append(rec)
    out = {}
    for key, recs in groups.items():
        score_task = any(r["score_task"] for r in recs)
        found = {"cost_usd": [r["agent"].get("cost_usd") for r in recs],
                 "turns": [r["agent"].get("num_turns") for r in recs],
                 **{f"metric:{name}": [r["metrics"].get(name) for r in recs] for name in metrics}}
        if score_task:
            found["score"] = [0.0 if r["grade"]["status"] == "grader_failure" else (r["grade"]["score"] or 0.0)
                              for r in recs]
        else:
            found["gate"] = [float(bool(r["grade"]["success"])) for r in recs]
        nums = {measure: _finite(vals) for measure, vals in found.items()}
        score_max_vals = [r.get("score_max") for r in recs]
        maxes = set(_finite(score_max_vals))  # cells of two eval trees may differ
        # If any cell has unknown max, or maxes differ, top is None; otherwise take the single value
        has_unknown_max = any(v is None for v in score_max_vals)
        top = (maxes.pop() if len(maxes) == 1 and not has_unknown_max else None) if score_task else 1.0
        # unmeasured: a grader failure, or an agent outage (an error result with no or one turn, or a non-zero exit
        # with no turn count); either way
        # its 0 is not a measurement
        grader_failed = any(r["grade"]["status"] == "grader_failure" or _agent_outage(r) for r in recs)
        out[key] = {"kind": "score" if score_task else "gate", "grader_failed": grader_failed,
                    "top": top,
                    "n": {measure: len(v) for measure, v in nums.items()},
                    "values": {measure: (sum(v) / len(v) if v else None) for measure, v in nums.items()}}
    return out


def _agent_outage(rec: dict[str, Any]) -> bool:
    """A cell whose agent never ran: an error result with no turn count or at most one turn, or a harness that
    exited non-zero before it reported any turn (no result record, so no ``is_error``) and was not killed for
    its timeout: an agent killed at its timeout did attempt the task."""
    agent = rec.get("agent") or {}
    turns = _finite([agent.get("num_turns")])  # a count that is not a number is no count, as in the turns row
    if agent.get("is_error") and (not turns or turns[0] <= 1):
        return True
    rc = agent.get("rc")
    crashed = isinstance(rc, int) and not isinstance(rc, bool) and rc != 0
    return crashed and not turns and not agent.get("timed_out")


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


def _finite(values: list[Any]) -> list[float]:
    return [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]


def _mean(values: list[Any]) -> float | None:
    nums = _finite(values)
    return sum(nums) / len(nums) if nums else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _p(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"
