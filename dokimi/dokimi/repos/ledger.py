# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``repo compare --ledger``: one verdict per guidance rule, from the cases it was written to prevent.

A ledger (``repo_ledger.v1``, dokimi/docs/rule-ledger.md) gives each rule its hypothesis, the arm that
carries it, the task ids of the cases standing for the failures it prevents, and the date it was last
reviewed. Each rule is compared with its baseline by :func:`.compare.compare` over its own cases, so the
pairing, headroom and provenance warnings are those of ``repo compare``. The rules' p values are
Holm-corrected together here, not in the comparison's own family.

Verdicts are computed, cutting is not: ``keep`` (the arm beats its baseline on the linked cases at
Holm-adjusted p below alpha), ``review`` (the baseline beats it at that level), ``insufficient-evidence``
(anything else, with the reason). A review date older than the policy allows is a warning, not a verdict.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from dokimi.artifacts import schema_errors
from dokimi.eval.stats import adjust_pvalues

from .compare import PRIMARY, compare, load_batches, min_sign_flip_p, parse_selector
from .compare_reach import ALPHA
from .compare_tasks import CompareError

KEEP, REVIEW, INSUFFICIENT = "keep", "review", "insufficient-evidence"
DEFAULT_REVIEW_DAYS = 180
_EPS = 1e-9  # a mean delta this close to zero is a tie, as in compare


class LedgerError(ValueError):
    """A ledger that cannot be read or does not validate; the message says where."""


@dataclass(frozen=True)
class Rule:
    id: str
    hypothesis: str
    arm: str
    cases: tuple[str, ...]
    reviewed: date
    baseline: str | None
    text: str | None
    source: str | None


@dataclass(frozen=True)
class Ledger:
    rules: tuple[Rule, ...]
    alpha: float
    review_within_days: int


def load_ledger(path: Path) -> Ledger:
    """The ledger in ``path``, validated against ``repo_ledger.v1`` and checked for duplicate ids."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LedgerError(f"{path}: cannot read the ledger: {exc}") from exc
    errors = schema_errors("repo_ledger.v1", raw)
    if errors:
        raise LedgerError(f"{path}: " + "; ".join(errors))
    rules, seen = [], set()
    for entry in raw["rules"]:
        rule = _rule(entry, path)
        if rule.id in seen:
            raise LedgerError(f"{path}: rule id {rule.id!r} is listed twice")
        seen.add(rule.id)
        rules.append(rule)
    policy = raw.get("policy", {})
    return Ledger(tuple(rules), float(policy.get("alpha", ALPHA)),
                  int(policy.get("review_within_days", DEFAULT_REVIEW_DAYS)))


def _rule(entry: dict[str, Any], path: Path) -> Rule:
    try:
        parse_selector(entry["arm"])
        if entry.get("baseline") is not None:
            parse_selector(entry["baseline"])
        reviewed = date.fromisoformat(entry["reviewed"])
    except ValueError as exc:  # a bad selector (CompareError is one) or an impossible date such as 2026-02-30
        raise LedgerError(f"{path}: rule {entry['id']!r}: {exc}") from exc
    return Rule(id=entry["id"], hypothesis=entry["hypothesis"], arm=entry["arm"], cases=tuple(entry["cases"]),
                reviewed=reviewed, baseline=entry.get("baseline"), text=entry.get("rule"), source=entry.get("source"))


def evaluate(ledger: Ledger, batch_dirs: list[Path], baseline: str, models: list[str] | None = None,
             seed: int = 0, today: date | None = None, ledger_name: str = "") -> dict[str, Any]:
    """One result per rule and model: paired statistics, Holm-adjusted p, verdict and reason."""
    today = today or datetime.now(UTC).date()
    known = {c["task"] for c in load_batches(batch_dirs)[0]}
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    for rule in ledger.rules:
        rule_entries = _rule_entries(rule, batch_dirs, baseline, models, known, seed, warnings)
        age = (today - rule.reviewed).days
        stale = age < 0 or age > ledger.review_within_days
        if age < 0:
            note = f"review date {rule.reviewed.isoformat()} is after {today.isoformat()}: check the date"
        else:
            note = (f"review date {rule.reviewed.isoformat()} is {age} days old (policy: "
                    f"{ledger.review_within_days}): re-review the rule")
        for entry in rule_entries:
            entry.update(reviewed=rule.reviewed.isoformat(), days_since_review=age, stale=stale)
            if stale:  # a new list: error entries of one rule share their head's warnings list
                entry["warnings"] = [*entry["warnings"], note]
        entries += rule_entries
    _holm(entries)
    for entry in entries:
        entry["verdict"], entry["reason"] = _verdict(entry, ledger.alpha)
    return {"ledger": ledger_name, "alpha": ledger.alpha, "review_within_days": ledger.review_within_days,
            "today": today.isoformat(), "family": sum(_in_family(e) for e in entries),
            "rules": entries, "warnings": list(dict.fromkeys(warnings))}


def _head(rule: Rule, baseline: str, linked: int, missing: list[str]) -> dict[str, Any]:
    return {"rule": rule.id, "rule_text": rule.text, "source": rule.source, "hypothesis": rule.hypothesis,
            "arm": rule.arm, "baseline": baseline, "linked": linked, "missing": missing, "model": None,
            "measure": None, "paired": 0, "error": None, "mixed": False, "changed": 0, "headroom": None,
            "baseline_mean": None, "treatment_mean": None, "delta": {"mean": None, "lo": None, "hi": None},
            "up": 0, "down": 0, "tied": 0, "p": None, "p_holm": None, "warnings": []}


def _rule_entries(rule: Rule, batch_dirs: list[Path], default_baseline: str, models: list[str] | None,
                  known: set[str], seed: int, warnings: list[str]) -> list[dict[str, Any]]:
    """A rule's results: one per model with a paired gate or score measure, else one that says why not."""
    missing = [c for c in rule.cases if c not in known]
    present = [c for c in rule.cases if c in known]
    base = rule.baseline or default_baseline
    head = _head(rule, base, len(rule.cases), missing)
    if not present:
        return [{**head, "error": "no linked case has a finished cell"}]
    entries: list[dict[str, Any]] = []
    # One comparison per --model: a model with no cell among this rule's cases fails only its own comparison.
    for model in dict.fromkeys(models) if models else [None]:  # a repeated --model is one comparison
        entries += _compared(rule, head, batch_dirs, base, [model] if model else None, present, seed, warnings)
    per_model = Counter(e["model"] for e in entries if not e["error"] and e["measure"] in PRIMARY)
    for entry in entries:
        entry["mixed"] = not entry["error"] and per_model[entry["model"]] > 1
    return entries


def _compared(rule: Rule, head: dict[str, Any], batch_dirs: list[Path], base: str, models: list[str] | None,
              present: list[str], seed: int, warnings: list[str]) -> list[dict[str, Any]]:
    """The rule's results of one compare() call, with its linked cases that did not pair, as warnings."""
    try:
        summary = compare(batch_dirs, base, [rule.arm], models, (), seed=seed, tasks=present)
    except CompareError as exc:  # the arm or the baseline has no cell among this rule's cases
        return [{**head, "model": models[0] if models else None, "error": str(exc)}]
    model = models[0] if models else None
    warnings.extend(summary["warnings"])
    linked = [u for u in summary["unpaired"] if u["task"] in present]
    rows = [r for r in summary["comparisons"] if r["measure"] in PRIMARY]
    if not rows:
        reasons = sorted({u["reason"] for u in linked})
        return [{**head, "model": model, "error": "; ".join(reasons) or "no linked case has cells in both arms"}]
    out = [_entry(head, r) for r in rows]
    for entry in out:
        own = [u for u in linked if u["model"] == entry["model"]]
        if own:
            entry["warnings"].append(f"{len({u['task'] for u in own})} linked case(s) not paired: "
                                     + "; ".join(sorted({u["reason"] for u in own})))
    for unpaired in sorted({u["model"] for u in linked} - {r["model"] for r in rows}):  # not measured, and named
        reasons = sorted({u["reason"] for u in linked if u["model"] == unpaired})
        out.append({**head, "model": unpaired, "error": "; ".join(reasons)})
    return out


def _entry(head: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    d = row["delta"]
    out = {**head, "model": row["model"], "measure": row["measure"], "paired": row["n_tasks"],
           "baseline_mean": row["baseline_mean"], "treatment_mean": row["treatment_mean"],
           "delta": {"mean": d["mean"], "lo": d["lo"], "hi": d["hi"]}, "up": row["up"], "down": row["down"],
           "tied": row["tied"], "p": row["p"], "headroom": row["headroom"], "mixed": False,
           "changed": row["up"] + row["down"], "warnings": []}
    if row["headroom"]:
        out["warnings"].append(f"{row['measure']} headroom: {row['headroom']} (the cases cannot show an effect)")
    return out


def _in_family(entry: dict[str, Any]) -> bool:
    """A result has a p and is not mixed: a gate-and-score rule has no single test, so it takes no slot."""
    return entry["p"] is not None and not entry["mixed"]


def _holm(entries: list[dict[str, Any]]) -> None:
    """``p_holm`` over every result in the family (the exact adjusted value); the others get none."""
    scored = [e for e in entries if _in_family(e)]
    for entry, adjusted in zip(scored, adjust_pvalues([e["p"] for e in scored], rounded=False), strict=True):
        entry["p_holm"] = adjusted


def _verdict(entry: dict[str, Any], alpha: float) -> tuple[str, str]:
    """The verdict of one result and why. Cutting a rule is left to a person."""
    if entry["error"] or not entry["paired"]:
        return INSUFFICIENT, f"not measured: {entry['error'] or 'no linked case has cells in both arms'}"
    if entry["mixed"]:
        return INSUFFICIENT, ("the linked cases mix gate and score tasks: link one kind of task per rule "
                              "(not in the Holm family)")
    if entry["p_holm"] is None:
        return INSUFFICIENT, "every linked case ties: there is no difference to test"
    p, mean = entry["p_holm"], entry["delta"]["mean"]
    if p < alpha and abs(mean) > _EPS:
        if mean > 0:
            return KEEP, f"beats {entry['baseline']} on its linked cases (p_holm {p:.4f} < {alpha})"
        return REVIEW, (f"{entry['baseline']} beats it on its linked cases (p_holm {p:.4f} < {alpha}): "
                        "a person decides whether to fix or cut the rule")
    reason = f"no significant difference from {entry['baseline']} (p_holm {p:.4f}, alpha {alpha})"
    if min_sign_flip_p(entry["changed"]) >= alpha:
        reason += f"; with {entry['changed']} changed case(s) no outcome reaches p < {alpha}: link more cases"
    return INSUFFICIENT, reason


def format_ledger(result: dict[str, Any]) -> str:
    """The rule results as text: one block per rule and model, the verdict first."""
    lines = [f"rule ledger {result['ledger']}: alpha {result['alpha']}, Holm over {result['family']} "
             f"result(s), reviews checked as of {result['today']}"]
    for e in result["rules"]:
        where = f"{e['rule']}: {e['arm']} vs {e['baseline']}" + (f", model {e['model']}" if e["model"] else "")
        lines.append(f"{where}, {e['measure'] or 'not measured'}: {e['verdict']}")
        if e["paired"]:
            d = e["delta"]
            ci = "-" if d["lo"] is None else f"[{d['lo']:+.3f}, {d['hi']:+.3f}]"
            lines.append(f"  paired {e['paired']} of {e['linked']} linked: pass rate {_num(e['baseline_mean'])} -> "
                         f"{_num(e['treatment_mean'])}, delta {d['mean']:+.3f} {ci}, p {_p(e['p'])}, "
                         f"p_holm {_p(e['p_holm'])}, up/down/tied {e['up']}/{e['down']}/{e['tied']}")
        lines.append(f"  {e['reason']}")
        if e["missing"]:
            lines.append(f"  no finished cell for: {', '.join(e['missing'])}")
        lines.append(f"  hypothesis: {e['hypothesis']}")
        lines += [f"  warning: {w}" for w in e["warnings"]]
    lines += [f"warning: {w}" for w in result["warnings"]]
    return "\n".join(lines)


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _p(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"
