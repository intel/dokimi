# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Verifier red team: do a question's grading checks reject the answers they should?

Each question with a ``grading`` block is graded against five synthetic answers:

- ``canary``: the right answer, built from the one literal check; it must pass.
- ``wrong``: a planted wrong answer; it must fail.
- ``empty``: the empty answer; it must fail.
- ``hedge``: a non-committal answer that carries no check value; it must fail.
- ``stuffed``: every literal check value listed with no sentence around it; it must fail. Built only
  for a block of one literal check (or one negated check), since a block that needs every value, or
  several checks that each accept one value, is satisfied by the stuffed answer by design.

An answer the grader accepts when it should reject it (or rejects a canary) is a violation, and
any violation blocks the run. A run that probed no question at all (nothing graded, or only judge
checks without a judge) is ``inconclusive``: it is not a pass, and it exits 1 like a blocked run.
A probe the grader cannot score (a judge check with no judge model, a regex past its timeout) is
reported as unscored. The canary is only built for a question whose grading is one literal check;
other questions get the other attacks alone.

Nothing here calls a model unless the caller passes ``ask`` (the judge of judge checks).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from dokimi.eval.reference_checks import Ask, grade
from dokimi.eval.reference_run import grading_by_id

ATTACKS = ("canary", "wrong", "empty", "hedge", "stuffed")
EXPECTED = {"canary": True, "wrong": False, "empty": False, "hedge": False, "stuffed": False}
WRONG = "PLANTED-WRONG-ANSWER-7731"
HEDGE = ("I am not certain. It could be several things, depending on the setup, "
         "and I cannot say which one applies here.")
BLOCKED, PASS, INCONCLUSIVE = "blocked", "pass", "inconclusive"

_LITERAL = frozenset({"match", "fuzzy", "exact", "choice", "json"})


def _literal_values(check: Mapping[str, Any]) -> list[Any]:
    raw = check.get("values", check.get("value"))
    if raw is None:
        return []
    return list(raw) if isinstance(raw, (list, tuple)) else [raw]


def canary_for(grading: Mapping[str, Any]) -> Optional[str]:
    """The right answer for a grading block of one literal check, or None when none is built."""
    checks = grading.get("checks", [])
    if len(checks) != 1 or checks[0].get("type") not in _LITERAL or checks[0].get("negate"):
        return None
    check = checks[0]
    kind, values = check["type"], _literal_values(check)
    mode, location = str(check.get("mode", "any")), str(check.get("location", "end"))
    if not values or (len(values) > 1 and mode == "all"):
        return None
    text = str(values[0])
    if kind == "choice":
        return "ANSWER: " + ", ".join(sorted(str(v).strip().upper() for v in values))
    if kind == "json":
        return json.dumps(values[0])
    if kind == "exact" or (kind == "match" and location == "exact"):
        return text
    if kind == "match" and location == "begin":
        return f"{text} is the answer."
    return f"The answer is {text}."


def stuffed_for(grading: Mapping[str, Any]) -> Optional[str]:
    """Every value of the block's one literal check, listed with no sentence around them.

    Only a block of one literal check is stuffed, and a positive check must accept any one of its
    values (mode ``any``): a block that needs every value, or several checks, is satisfied by the
    stuffed answer by design, so accepting it is not a violation. A negated check fails the stuffed
    answer that carries its value, so it is always a real attack. One value listed alone is the right
    answer itself, not stuffing. A choice check is left out: its answer is a bare letter.
    """
    checks = grading.get("checks", [])
    if len(checks) != 1 or checks[0].get("type") not in _LITERAL - {"choice"}:
        return None
    check = checks[0]
    if not check.get("negate") and str(check.get("mode", "any")) == "all":
        return None
    values = [str(v) for v in _literal_values(check)]
    return " ".join(values) if len(values) >= 2 else None


def attacks(grading: Mapping[str, Any]) -> dict[str, Optional[str]]:
    """The synthetic answer for each attack; None where the attack does not apply to the block.

    A ``negate`` check passes an answer that lacks its value, so a wrong or empty answer passes it
    by design; ``stuffed`` still runs, since it carries the negated value and must fail. The hedge is
    fixed text, so it is skipped when a check value occurs in it (the grader would accept it).
    """
    checks = grading.get("checks", [])
    negated = any(c.get("negate") for c in checks)
    values = [str(v).strip().lower() for c in checks if c.get("type") in _LITERAL - {"choice"}
              for v in _literal_values(c)]
    clashes = any(v and v in HEDGE.lower() for v in values)
    return {"canary": canary_for(grading),
            "wrong": None if negated else WRONG,
            "empty": None if negated else "",
            "hedge": None if negated or clashes else HEDGE,
            "stuffed": stuffed_for(grading)}


@dataclass(frozen=True)
class Probe:
    attack: str
    passed: Optional[bool]
    detail: str = ""

    @property
    def expected(self) -> bool:
        return EXPECTED[self.attack]

    @property
    def violation(self) -> bool:
        return self.passed is not None and self.passed != self.expected

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "expected": self.expected, "violation": self.violation,
                "detail": self.detail}


def probe(grading: Mapping[str, Any], question: str, ask: Optional[Ask] = None) -> dict[str, Probe]:
    """Grade every applicable attack against one grading block."""
    out: dict[str, Probe] = {}
    for name, answer in attacks(grading).items():
        if answer is None:
            continue
        g = grade(grading, question, answer, ask)
        out[name] = Probe(name, g.passed, _detail(g))
    return out


def _detail(g: Any) -> str:
    return "; ".join(c.detail for c in g.checks if c.detail)


def run(questions: list[Mapping[str, Any]], *, ask: Optional[Ask] = None) -> dict[str, Any]:
    """Red-team every question with a grading block. Raises ValueError for a block that cannot be graded."""
    gradings = grading_by_id(questions)
    rows: list[dict[str, Any]] = []
    for q in questions:
        qid = str(q.get("id"))
        if qid not in gradings:
            continue
        text = str(q.get("question") or q.get("text") or "")
        probes = probe(gradings[qid], text, ask)
        rows.append({"question_id": qid, "canary": "canary" in probes,
                     "probes": {name: p.as_dict() for name, p in probes.items()}})
    violations = [{"question_id": r["question_id"], "attack": name, "passed": p["passed"]}
                  for r in rows for name, p in r["probes"].items() if p["violation"]]
    summary = _summary(rows)
    if violations:
        status = BLOCKED
    else:
        status = PASS if summary["probed"] else INCONCLUSIVE
    return {
        "status": status,
        "attacks": _attack_rates(rows),
        "violations": violations,
        "questions": rows,
        "summary": summary,
    }


def _attack_rates(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in ATTACKS:
        scored = [r["probes"][name]["passed"] for r in rows if name in r["probes"]]
        scored = [p for p in scored if p is not None]
        accepted = sum(1 for p in scored if p)
        out[name] = {"expected": "pass" if EXPECTED[name] else "fail", "scored": len(scored),
                     "accepted": accepted, "rate": round(accepted / len(scored), 4) if scored else None}
    return out


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    probes = [p for r in rows for p in r["probes"].values()]
    return {
        "questions": len(rows),
        "probed": sum(1 for r in rows if any(p["passed"] is not None for p in r["probes"].values())),
        "no_canary": sum(1 for r in rows if not r["canary"]),
        "unscored": sum(1 for p in probes if p["passed"] is None),
        "violations": sum(1 for p in probes if p["violation"]),
    }


def format_table(result: dict[str, Any]) -> str:
    lines = [f"{'attack':<8} {'expected':<9} {'scored':>6} {'accepted':>8} {'rate':>6}"]
    for name, a in result["attacks"].items():
        rate = "-" if a["rate"] is None else f"{a['rate']:.2f}"
        lines.append(f"{name:<8} {a['expected']:<9} {a['scored']:>6} {a['accepted']:>8} {rate:>6}")
    for v in result["violations"][:20]:
        verb = "accepted" if v["attack"] != "canary" else "rejected"
        lines.append(f"  violation: question {v['question_id']}: {v['attack']} answer {verb}")
    if len(result["violations"]) > 20:
        lines.append(f"  ... {len(result['violations']) - 20} more violations in the artifact")
    s = result["summary"]
    lines.append(f"status {result['status']}: {s['questions']} questions, {s['probed']} probed, "
                 f"{s['no_canary']} without a canary, {s['unscored']} probes unscored, "
                 f"{s['violations']} violations")
    return "\n".join(lines)
