# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Verifier red team: do a question's grading checks reject the answers they should?

Each question with a ``grading`` block is graded against five synthetic answers:

- ``canary``: the right answer, built from the one literal check; it must pass.
- ``wrong``: a planted wrong answer; it must fail. Its text is picked so that no positive literal
  check accepts it; the probe is skipped only when no candidate is clean.
- ``empty``: the empty answer; it must fail.
- ``hedge``: a non-committal answer that carries no check value; it must fail.
- ``stuffed``: every literal check value listed with no sentence around it; it must fail. Built for
  a positive check of two or more values (mode ``any``) that does not read the list where it stands:
  bare for ``exact``, ``json`` and location-``exact`` checks, between two decoys for a ``match`` at
  ``begin`` or ``end``, after a JSON decoy for a ``json`` check with ``contains``. It is not built for ``fuzzy``, numeric or location-``any`` checks, a block that
  needs every value, or several checks that each accept one value: the grader is right to accept the
  stuffed answer there. Also built
  for a negated check of one or more values, whose stuffed answer carries the forbidden value; not
  built for a negated ``exact``, ``json`` or location-``exact`` match, which the listed values never
  equal, nor for a negated ``mode: all`` match, whose stuffed answer can pass the negation by its
  location (``Paris Lyon`` ends with one value only). The skip covers every negated ``mode: all``
  match, which is wider than that case and never a false block.

An answer the grader accepts when it should reject it (or rejects a canary) is a violation, and
any violation blocks the run. A run with a question whose correctness no probe measured (a judge check without a judge, a block of
only negated checks) is ``inconclusive``, and so is one with nothing graded: it is not a pass, and it exits 1
like a blocked run. Only a run that measured every graded question and found no violation is ``pass``.
A probe the grader cannot score (a judge check with no judge model, a regex past its timeout) is
reported as unscored. The canary is only built for a question whose grading is one literal check;
other questions get the other attacks alone.

A ``negate`` check passes a wrong, empty or hedged answer by design, so a block of only negated
checks gets none of those three attacks and is reported as ``negated_only``; its stuffed answer still
runs. A block with a positive check is graded as written, ``threshold`` included: a negated check's
share of the score counts, so a block whose negated checks lift a wrong answer over the line is blocked.

Nothing here calls a model unless the caller passes ``ask`` (the judge of judge checks).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import permutations
from typing import Any, Mapping, Optional

from dokimi.eval.reference_checks import Ask, _number, check_values, grade, grade_check
from dokimi.eval.reference_run import grading_by_id

ATTACKS = ("canary", "wrong", "empty", "hedge", "stuffed")
EXPECTED = {"canary": True, "wrong": False, "empty": False, "hedge": False, "stuffed": False}
# Planted wrong answers, tried in order; the first that no positive literal check accepts is used.
WRONG_CANDIDATES = ("PLANTED-WRONG-ANSWER-7731", "QZX-BOGUS-REPLY-4096", "ZKV-MISMATCH-2508", "XJQ-NONSENSE-6349")
# JSON decoys for a json check with contains, which reads the first JSON object or array in the text.
JSON_DECOYS = ('{"planted": "wrong"}', '{"planted": "other"}')
HEDGE = ("I am not certain. It could be several things, depending on the setup, "
         "and I cannot say which one applies here.")
BLOCKED, PASS, INCONCLUSIVE = "blocked", "pass", "inconclusive"
# The attacks a negated check makes unfit for a block: the answers it passes by design.
NEGATION_SKIPPED = ("wrong", "empty", "hedge")

_LITERAL = frozenset({"match", "fuzzy", "exact", "choice", "json"})


def canary_for(grading: Mapping[str, Any]) -> Optional[str]:
    """The right answer for a grading block of one literal check, or None when none is built."""
    checks = grading.get("checks", [])
    if len(checks) != 1 or checks[0].get("type") not in _LITERAL or checks[0].get("negate"):
        return None
    check = checks[0]
    kind, values = check["type"], check_values(check)
    mode, location = str(check.get("mode", "any")), str(check.get("location", "end"))
    if not values or (len(values) > 1 and mode == "all"):
        return None
    text = str(values[0])
    if kind == "match" and check.get("numeric") and (number := _number(text)) is not None:
        # The numeric matcher reads a number from the answer with a digit before the point, so "-.5"
        # would be read as 5: the canary spells the number the grader parsed, in plain decimal.
        text = f"{number:.12f}".rstrip("0").rstrip(".")
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


def _reads_first_json(check: Mapping[str, Any]) -> bool:
    """Whether a json check with ``contains`` reads the first JSON in the answer, not the whole answer."""
    return check.get("type") == "json" and bool(check.get("contains"))


def stuffed_for(grading: Mapping[str, Any]) -> Optional[str]:
    """The block's one literal check's values, listed with no sentence around them; None when not built.

    A positive check needs two or more values and must accept any one of them (mode ``any``): a block
    that needs every value, or several checks, is satisfied by the stuffed answer by design, so
    accepting it is not a violation. A single positive value listed alone is the right answer itself,
    not stuffing. The list is an attack only where the check does not read it:

    - a check on the whole answer (``exact``, ``json`` without ``contains``, ``match`` at ``exact``): the bare list;
    - a ``match`` at ``begin`` or ``end``: the list between two decoys the check rejects, so no accepted
      value is at the place the check reads;
    - a ``json`` check with ``contains`` reads the first JSON in the text, so the list goes after two JSON
      decoys the check rejects: a bare list would start with a listed value and be accepted;
    - ``match`` at ``any``, a numeric ``match`` and ``fuzzy`` find a listed value wherever it is, so the
      grader is right to accept it and no probe is built.

    A negated check is stuffed with its one or more values: the stuffed answer carries the forbidden
    value, so it must fail, unless the check compares the whole answer (see ``_whole_answer``), which the
    listed values never equal, or is ``mode: all``, which the stuffed answer can pass by its location
    (the skip is kept for every such check, for simplicity), and the list is built only where the same
    check without ``negate`` accepts it, so a value the grader reads as another one is no attack.
    A choice check is left out: its answer is a bare letter.
    """
    checks = grading.get("checks", [])
    if len(checks) != 1 or checks[0].get("type") not in _LITERAL - {"choice"}:
        return None
    check = checks[0]
    values = [str(v) for v in check_values(check)]
    mode = str(check.get("mode", "any"))
    if check.get("negate"):
        bare = " ".join(values)
        built = bool(values) and not _whole_answer(check) and mode != "all"
        # Built only when the grader reads the value out of the list, as the positive check does.
        carries = grade_check({**check, "negate": False}, "", bare, None).passed
        return bare if built and carries else None
    if len(values) < 2 or mode == "all":
        return None
    if _whole_answer(check) and not _reads_first_json(check):
        bare = " ".join(values)
        # The list is no attack where the grader's own rules accept it (``b a`` is ``b`` once the article goes).
        return None if grade_check(check, "", bare, None).passed else bare
    candidates: tuple[str, ...]
    if _reads_first_json(check):
        candidates = JSON_DECOYS
    elif check.get("type") != "match" or check.get("numeric") or str(check.get("location", "end")) == "any":
        return None
    else:
        candidates = WRONG_CANDIDATES
    decoys = [w for w in candidates if not grade_check(check, "", w, None).passed]
    # Each decoy is rejected alone, but the whole answer can still end (or begin) with a value: the first
    # decoy pair whose complete answer the check rejects is used, and none is built when no pair is.
    for before, after in permutations(decoys, 2):
        stuffed = f"{before} {' '.join(values)} {after}"
        if not grade_check(check, "", stuffed, None).passed:
            return stuffed
    return None


def _positive_checks(grading: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [c for c in grading.get("checks", []) if not c.get("negate")]


def negated_only(grading: Mapping[str, Any]) -> bool:
    """Whether every check of the block is negated, so no answer is wrong by its positive checks."""
    checks = grading.get("checks", [])
    return bool(checks) and all(c.get("negate") for c in checks)


def _accepts(grading: Mapping[str, Any], text: str) -> bool:
    """Whether a non-negated literal check of the block accepts the text on its own, by the grader's rules."""
    return any(grade_check(c, "", text, None).passed for c in _positive_checks(grading) if c.get("type") in _LITERAL)


def wrong_for(grading: Mapping[str, Any]) -> Optional[str]:
    """The first planted wrong answer that no positive literal check of the block accepts, or None."""
    return next((w for w in WRONG_CANDIDATES if not _accepts(grading, w)), None)


def attacks(grading: Mapping[str, Any]) -> dict[str, Optional[str]]:
    """The synthetic answer for each attack; None where the attack does not apply to the block.

    The wrong, empty and hedge answers apply when the block has a non-negated check, and are graded
    against the whole block as written. A block of only negated checks gets none of the three.
    ``stuffed`` still runs, since it carries the negated value and must fail. The planted wrong answer
    is chosen per block so that no positive check accepts it, and the hedge is fixed text, so it is
    skipped when a positive check accepts it (each is asked with the grader's own rules, so a numeric
    match that reads a number out of the planted answer, or a short value the check does not find in the
    hedge, are judged as the grader judges them).
    """
    applies = bool(_positive_checks(grading))
    clashes = _accepts(grading, HEDGE)
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
    for name, answer in attacks(grading).items():
        if answer is None:
            continue
        g = grade(grading, question, answer, ask)
        # A check left unrun (no judge, or a regex past its timeout) makes a pass on the rest no measurement;
        # with a threshold it is no result either, so the score is taken over the counted checks alone.
        partial = bool(g.unjudged or g.unrun)
        passed = None if partial and (g.passed or g.threshold is not None) else g.passed
        out[name] = Probe(name, passed, _detail(g))
    return out


def _detail(g: Any) -> str:
    return "; ".join(c.detail for c in g.checks if c.detail)


def run(questions: list[Mapping[str, Any]], *, ask: Optional[Ask] = None) -> dict[str, Any]:
    """Red-team every question with a grading block. Raises ValueError for a block that cannot be graded."""
    grading_by_id(questions)  # refuses a set with a block that cannot be graded; the blocks are read per question
    rows: list[dict[str, Any]] = []
    for q in questions:
        if "grading" not in q:
            continue
        qid, grading = str(q.get("id")), q["grading"]  # not looked up by id: ids may repeat
        text = str(q.get("question") or q.get("text") or "")
        probes = probe(grading, text, ask)
        rows.append({"question_id": qid, "canary": "canary" in probes,
                     "negated_only": negated_only(grading),
                     "probes": {name: p.as_dict() for name, p in probes.items()}})
    violations = [{"question_id": r["question_id"], "attack": name, "passed": p["passed"]}
                  for r in rows for name, p in r["probes"].items() if p["violation"]]
    summary = _summary(rows)
    if violations:
        status = BLOCKED
    else:
        status = PASS if rows and not summary["unprobed"] else INCONCLUSIVE
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


def _unmeasured(row: Mapping[str, Any]) -> str:
    """Why a question's correctness was not measured, or "" when a probe scored it.

    A block of only negated checks gets no wrong, empty or hedge probe: the real grader accepts any
    answer without the forbidden value, so nothing was verified about it, whatever its stuffed probe said.
    """
    if row["negated_only"]:
        return "only negated checks"
    if not any(p["passed"] is not None for p in row["probes"].values()):
        return "no probe scored"
    return ""


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    probes = [p for r in rows for p in r["probes"].values()]
    unprobed = sum(1 for r in rows if _unmeasured(r))
    return {
        "questions": len(rows),
        "probed": len(rows) - unprobed,
        "unprobed": unprobed,
        "no_canary": sum(1 for r in rows if not r["canary"]),
        "negated_only": sum(1 for r in rows if r["negated_only"]),
        "unscored": sum(1 for p in probes if p["passed"] is None),
        "violations": sum(1 for p in probes if p["violation"]),
    }


def format_table(result: dict[str, Any], output: Any = None) -> str:
    """The pass-rate table; ``output`` is the ``--out`` path when an artifact is written, else None."""
    lines = [f"{'attack':<8} {'expected':<9} {'scored':>6} {'accepted':>8} {'rate':>6}"]
    for name, a in result["attacks"].items():
        rate = "-" if a["rate"] is None else f"{a['rate']:.2f}"
        lines.append(f"{name:<8} {a['expected']:<9} {a['scored']:>6} {a['accepted']:>8} {rate:>6}")
    for v in result["violations"][:20]:
        verb = "accepted" if v["attack"] != "canary" else "rejected"
        lines.append(f"  violation: question {v['question_id']}: {v['attack']} answer {verb}")
    if len(result["violations"]) > 20:
        more = len(result["violations"]) - 20
        lines.append(f"  ... {more} more violations in the artifact" if output
                     else f"  ... {more} more violations; rerun with --out FILE to list them all")
    s = result["summary"]
    if s["negated_only"]:
        lines.append(f"  {s['negated_only']} questions with only negated checks: "
                     f"{', '.join(NEGATION_SKIPPED[:-1])} and {NEGATION_SKIPPED[-1]} not run")
    unmeasured = [f"question {r['question_id']} ({why})" for r in result["questions"] if (why := _unmeasured(r))]
    if unmeasured:
        lines.append(f"  not measured: {', '.join(unmeasured[:20])}"
                     + (f", and {len(unmeasured) - 20} more" if len(unmeasured) > 20 else ""))
    lines.append(f"status {result['status']}: {s['questions']} questions, {s['probed']} probed, "
                 f"{s['unprobed']} unprobed, {s['no_canary']} without a canary, {s['unscored']} probes unscored, "
                 f"{s['violations']} violations")
    return "\n".join(lines)
