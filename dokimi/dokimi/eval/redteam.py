# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Verifier red team: do a question's grading checks reject the answers they should?

Each question with a ``grading`` block is graded against five synthetic answers:

- ``canary``: the right answer, built from the one literal check; it must pass.
- ``wrong``: a planted wrong answer; it must fail. Its text is picked so that no positive literal
  check value occurs in it; the probe is skipped only when no candidate is clean.
- ``empty``: the empty answer; it must fail.
- ``hedge``: a non-committal answer that carries no check value; it must fail.
- ``stuffed``: every literal check value listed with no sentence around it; it must fail. Built for
  a positive check of two or more values (mode ``any``), since a block that needs every value, or
  several checks that each accept one value, is satisfied by the stuffed answer by design. Also built
  for a negated check of one or more values, whose stuffed answer carries the forbidden value; not
  built for a negated ``exact``, ``json`` or location-``exact`` match, which the listed values never
  equal, nor for a negated ``mode: all`` match, whose stuffed answer can pass the negation by its
  location (``Paris Lyon`` ends with one value only). The skip covers every negated ``mode: all``
  match, which is wider than that case and never a false block.

An answer the grader accepts when it should reject it (or rejects a canary) is a violation, and
any violation blocks the run. A run that probed no question at all (nothing graded, or only judge
checks without a judge) is ``inconclusive``: it is not a pass, and it exits 1 like a blocked run.
A probe the grader cannot score (a judge check with no judge model, a regex past its timeout) is
reported as unscored. The canary is only built for a question whose grading is one literal check;
other questions get the other attacks alone.

A ``negate`` check passes a wrong, empty or hedged answer by design, so those three attacks grade
the block's non-negated checks alone. A block of only negated checks gets none of them and is
reported as ``negated_only``; its stuffed answer still runs.

Nothing here calls a model unless the caller passes ``ask`` (the judge of judge checks).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from dokimi.eval.reference_checks import Ask, grade, squad_normalize
from dokimi.eval.reference_run import grading_by_id

ATTACKS = ("canary", "wrong", "empty", "hedge", "stuffed")
EXPECTED = {"canary": True, "wrong": False, "empty": False, "hedge": False, "stuffed": False}
# Planted wrong answers, tried in order; the first that carries no positive check value is used.
WRONG_CANDIDATES = ("PLANTED-WRONG-ANSWER-7731", "QZX-BOGUS-REPLY-4096", "ZKV-MISMATCH-2508", "XJQ-NONSENSE-6349")
HEDGE = ("I am not certain. It could be several things, depending on the setup, "
         "and I cannot say which one applies here.")
BLOCKED, PASS, INCONCLUSIVE = "blocked", "pass", "inconclusive"
# The attacks a negated check makes unfit for a block: the answers it passes by design.
NEGATION_SKIPPED = ("wrong", "empty", "hedge")

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
    if kind == "match" and location == "end" and not check.get("ignore_punctuation", True):
        return f"The answer is {text}"  # the full stop would be the raw end of the answer, not the value
    return f"The answer is {text}."


def _whole_answer(check: Mapping[str, Any]) -> bool:
    """Whether the check compares the whole answer, so a listed value never equals it."""
    kind = check.get("type")
    return kind in ("exact", "json") or (kind == "match" and str(check.get("location", "end")) == "exact")


def stuffed_for(grading: Mapping[str, Any]) -> Optional[str]:
    """The block's one literal check's values, listed with no sentence around them; None when not built.

    A positive check needs two or more values and must accept any one of them (mode ``any``): a block
    that needs every value, or several checks, is satisfied by the stuffed answer by design, so
    accepting it is not a violation. A single positive value listed alone is the right answer itself,
    not stuffing. A negated check is stuffed with its one or more values: the stuffed answer carries
    the forbidden value, so it must fail, unless the check compares the whole answer (see
    ``_whole_answer``), which the listed values never equal, or is ``mode: all``, which the stuffed
    answer can pass by its location (the skip is kept for every such check, for simplicity). A choice
    check is left out: its answer is a bare letter.
    """
    checks = grading.get("checks", [])
    if len(checks) != 1 or checks[0].get("type") not in _LITERAL - {"choice"}:
        return None
    check = checks[0]
    values = [str(v) for v in _literal_values(check)]
    mode = str(check.get("mode", "any"))
    if check.get("negate"):
        built = bool(values) and not _whole_answer(check) and mode != "all"
        return " ".join(values) if built else None
    if len(values) < 2 or mode == "all":
        return None
    return " ".join(values)


def _positive_checks(grading: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [c for c in grading.get("checks", []) if not c.get("negate")]


def _positive_only(grading: Mapping[str, Any]) -> Mapping[str, Any]:
    """The grading block with its negated checks removed; the threshold and the rest are kept."""
    return {**grading, "checks": _positive_checks(grading)}


def negated_only(grading: Mapping[str, Any]) -> bool:
    """Whether every check of the block is negated, so no answer is wrong by its positive checks."""
    checks = grading.get("checks", [])
    return bool(checks) and all(c.get("negate") for c in checks)


def _contains(value: str, text: str) -> bool:
    """Whether the text carries the value: a raw substring, or one after the grader's normalisation."""
    raw = value.strip().lower()
    norm = squad_normalize(value)
    return (bool(raw) and raw in text.lower()) or (bool(norm) and norm in squad_normalize(text))


def _positive_values(grading: Mapping[str, Any]) -> list[str]:
    """The values of the block's non-negated literal checks, bare choice letters left out."""
    return [str(v) for c in _positive_checks(grading) if c.get("type") in _LITERAL - {"choice"}
            for v in _literal_values(c)]


def wrong_for(grading: Mapping[str, Any]) -> Optional[str]:
    """The first planted wrong answer that carries none of the block's positive values, or None."""
    values = _positive_values(grading)
    return next((w for w in WRONG_CANDIDATES if not any(_contains(v, w) for v in values)), None)


def attacks(grading: Mapping[str, Any]) -> dict[str, Optional[str]]:
    """The synthetic answer for each attack; None where the attack does not apply to the block.

    The wrong, empty and hedge answers apply when the block has a non-negated check: a negated
    check passes them by design, but a positive check still rejects them, so they run and the probe
    grades the non-negated checks alone. A block of only negated checks gets none of the three.
    ``stuffed`` still runs, since it carries the negated value and must fail. The planted wrong answer
    is chosen per block so that no positive value occurs in it, and the hedge is fixed text, so it is
    skipped when a positive value occurs in it (the grader would accept it).
    """
    positive = _positive_checks(grading)
    clashes = any(_contains(v, HEDGE) for v in _positive_values(grading))
    applies = bool(positive)
    return {"canary": canary_for(grading),
            "wrong": wrong_for(grading) if applies else None,
            "empty": "" if applies else None,
            "hedge": None if not applies or clashes else HEDGE,
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
    positive = _positive_only(grading)
    for name, answer in attacks(grading).items():
        if answer is None:
            continue
        block = positive if name in NEGATION_SKIPPED else grading
        g = grade(block, question, answer, ask)
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
                     "negated_only": negated_only(gradings[qid]),
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
        "unprobed": sum(1 for r in rows if not any(p["passed"] is not None for p in r["probes"].values())),
        "no_canary": sum(1 for r in rows if not r["canary"]),
        "negated_only": sum(1 for r in rows if r["negated_only"]),
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
    if s["negated_only"]:
        lines.append(f"  {s['negated_only']} questions with only negated checks: "
                     f"{', '.join(NEGATION_SKIPPED[:-1])} and {NEGATION_SKIPPED[-1]} not run")
    lines.append(f"status {result['status']}: {s['questions']} questions, {s['probed']} probed, "
                 f"{s['unprobed']} unprobed, {s['no_canary']} without a canary, {s['unscored']} probes unscored, "
                 f"{s['violations']} violations")
    return "\n".join(lines)
