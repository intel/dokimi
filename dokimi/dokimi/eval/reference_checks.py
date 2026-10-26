# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Grade an answer against the reference checks a question carries (its ``grading`` block).

Imported answer-only evals (Inspect AI, OpenAI evals, promptfoo, skill-creator; see
:mod:`dokimi.task_formats.imports`) say how an answer is graded in each tool's own terms. The
importers translate those terms into the format-neutral checks below, stored on the question
record, and this module grades every answer of an answers or arms file with them:

========== ============================================ ==========================================
type       passes when                                   native names it maps
========== ============================================ ==========================================
``match``  a value is found at ``location`` (``begin``,  Inspect ``match``/``includes``/``exact``,
           ``end``, ``any``, ``exact``), after the        OpenAI ``Match``/``Includes``, promptfoo
           ``ignore_case`` / ``ignore_punctuation`` /     ``equals``/``contains*``/``starts-with``
           ``strip`` / ``numeric`` normalisation
``exact``  SQuAD-normalised text equals a value           Inspect ``exact``
``fuzzy``  normalised answer and value contain each other OpenAI ``FuzzyMatch``
``regex``  the pattern is found                          promptfoo ``regex``, Inspect ``pattern``
``choice`` the ``ANSWER: <letters>`` line names exactly   Inspect ``choice`` (with
           the target letters                            ``multiple_choice``)
``json``   the answer parses as JSON (equal to a value,  OpenAI ``JsonMatch``, promptfoo
           when values are given)                        ``is-json``/``contains-json``
``judge``  the judge says the answer meets ``criterion``  Inspect ``model_graded_*``, OpenAI
           (or agrees with ``reference``)                ``ModelBasedClassify``, promptfoo
                                                         ``llm-rubric``/``factuality``,
                                                         skill-creator expectations
========== ============================================ ==========================================

``mode: all`` makes a multi-value check need every value (promptfoo ``contains-all``); ``negate``
inverts it (promptfoo ``not-*``). A check of type ``unsupported`` records what an importer could
not translate (code asserts, embeddings, cost); it is reported and never counted.

A ``judge`` check is one required point of :mod:`dokimi.eval.checklist`, asked in both
polarities, so a judge's yes/no bias scores 0.5 instead of a pass. A question's score is the
weighted mean of its counted checks; it passes when every counted check passes, or when the
score reaches ``threshold`` if the question sets one (promptfoo's test ``threshold``).
"""

from __future__ import annotations

import json
import math
import re
import string
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

#: Check types this module grades without a model.
DETERMINISTIC = frozenset({"match", "exact", "fuzzy", "regex", "choice", "json"})
JUDGED = frozenset({"judge"})
UNSUPPORTED = "unsupported"
KNOWN = DETERMINISTIC | JUDGED | {UNSUPPORTED}

_LOCATIONS = ("begin", "end", "any", "exact")

#: Asks the judge model one prompt; the :data:`dokimi.eval.checklist.Ask` contract.
Ask = Callable[..., str]


class CheckError(ValueError):
    """A grading block that cannot be graded as written."""


@dataclass(frozen=True)
class CheckResult:
    type: str
    passed: Optional[bool]
    score: Optional[float]
    weight: float = 1.0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "passed": self.passed, "score": self.score}
        if self.weight != 1.0:
            out["weight"] = self.weight
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class Grade:
    """One answer graded against one question's checks."""

    checks: list[CheckResult] = field(default_factory=list)
    threshold: Optional[float] = None

    @property
    def counted(self) -> list[CheckResult]:
        return [c for c in self.checks if c.type != UNSUPPORTED and c.score is not None and c.weight > 0]

    @property
    def score(self) -> Optional[float]:
        counted = self.counted
        total = sum(c.weight for c in counted)
        if not counted or total <= 0:
            return None
        return sum(c.weight * (c.score or 0.0) for c in counted) / total

    @property
    def passed(self) -> Optional[bool]:
        score = self.score
        if score is None:
            return None
        if self.threshold is not None:
            return score >= self.threshold
        return all(c.passed for c in self.counted)

    def as_dict(self) -> dict[str, Any]:
        score = self.score
        return {
            "aggregate": None if score is None else round(100 * score, 1),
            "passed": self.passed,
            "checks": [c.as_dict() for c in self.checks],
        }


# --- normalisation -----------------------------------------------------------------------------

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCT = str.maketrans("", "", string.punctuation)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def _strip_punct(text: str) -> str:
    return text.translate(_PUNCT)


def squad_normalize(text: str) -> str:
    """Casefold, drop punctuation and articles, collapse whitespace (SQuAD / Inspect ``exact``)."""
    return " ".join(_ARTICLES.sub(" ", _strip_punct(text.casefold())).split())


def _number(text: str) -> Optional[float]:
    try:
        value = float(text.replace(",", "").replace("$", "").strip("*_ ."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _numeric_match(answer: str, target: float, location: str) -> bool:
    found = [_number(m) for m in _NUMBER.findall(answer.replace(",", ""))]
    numbers = [n for n in found if n is not None]
    if not numbers:
        return False
    if location == "begin":
        numbers = numbers[:1]
    elif location == "end":
        numbers = numbers[-1:]
    elif location == "exact":
        whole = _number(answer)
        numbers = [whole] if whole is not None else []
    return any(math.isclose(n, target, rel_tol=1e-9, abs_tol=1e-9) for n in numbers)


def match_text(answer: str, value: str, *, location: str = "end", ignore_case: bool = True,
               ignore_punctuation: bool = True, strip: bool = True, numeric: bool = False) -> bool:
    """Whether ``value`` is at ``location`` in ``answer`` (Inspect ``match_str`` semantics)."""
    if location not in _LOCATIONS:
        raise CheckError(f"match location must be one of {', '.join(_LOCATIONS)}, not {location!r}")
    a, v = (answer.strip(), value.strip()) if strip else (answer, value)
    if numeric and (target := _number(v)) is not None:
        return _numeric_match(a, target, location)
    if ignore_case:
        a, v = a.casefold(), v.casefold()
    if ignore_punctuation:
        a, v = _strip_punct(a), _strip_punct(v)
        if strip:
            a, v = a.strip(), v.strip()
    if location == "begin":
        return a.startswith(v)
    if location == "end":
        return a.endswith(v)
    if location == "exact":
        return a == v
    return v in a


# --- multiple choice ---------------------------------------------------------------------------

_ANSWER_STRICT = re.compile(r"(?im)^ANSWER\s*:\s*([A-Za-z\d ,]+)\s*(?:$|\.)")
_ANSWER_LENIENT = re.compile(r"(?i)ANSWER\s*:\s*([A-Za-z\d ,]+)(?:[^\w]|$)")


def chosen_letters(answer: str) -> Optional[frozenset[str]]:
    """The letters of the last ``ANSWER: ...`` line (Inspect ``multiple_choice`` parsing), or None."""
    text = re.sub(r"\*\*\s*([A-Za-z\d][A-Za-z\d ,]*?)\s*\*\*", r"\1", answer)
    text = re.sub(r"\$\s*([A-Za-z\d][A-Za-z\d ,]*?)\s*\$", r"\1", text)
    text = re.sub(r"(?i)(^|[:,])(\s*)\(([A-Za-z\d])\)(?=\s*(?:,|\.|\n|\Z))", r"\1\2\3", text)
    found = _ANSWER_STRICT.findall(text) or _ANSWER_LENIENT.findall(text)
    if not found:
        return None
    letters = re.findall(r"[A-Za-z]", found[-1])
    return frozenset(x.upper() for x in letters) or None


# --- JSON --------------------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)


def parse_json(answer: str, *, contains: bool = False) -> tuple[bool, Any]:
    """``(parsed, value)``: the answer as JSON (one fenced block is unwrapped).

    With ``contains`` the first JSON object or array anywhere in the text counts
    (promptfoo ``contains-json``).
    """
    text = answer.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return True, json.loads(text)
    except ValueError:
        pass
    if contains:
        decoder = json.JSONDecoder()
        for i, ch in enumerate(text):
            if ch in "{[":
                try:
                    return True, decoder.raw_decode(text[i:])[0]
                except ValueError:
                    continue
    return False, None


# --- one check ---------------------------------------------------------------------------------


def _values(check: Mapping[str, Any]) -> list[Any]:
    raw = check.get("values", check.get("value"))
    if raw is None:
        return []
    return list(raw) if isinstance(raw, (list, tuple)) else [raw]


def _combine(hits: Sequence[bool], mode: str) -> bool:
    if mode not in ("any", "all"):
        raise CheckError(f"check mode must be 'any' or 'all', not {mode!r}")
    return bool(hits) and (all(hits) if mode == "all" else any(hits))


def _deterministic(check: Mapping[str, Any], answer: str) -> tuple[bool, str]:
    kind = check["type"]
    values = _values(check)
    mode = str(check.get("mode", "any"))
    if kind == "match":
        location = str(check.get("location", "end"))
        opt = {k: bool(check.get(k, k != "numeric")) for k in ("ignore_case", "ignore_punctuation", "strip", "numeric")}
        return _combine([match_text(answer, str(v), location=location, ignore_case=opt["ignore_case"],
                                    ignore_punctuation=opt["ignore_punctuation"], strip=opt["strip"],
                                    numeric=opt["numeric"]) for v in values], mode), ""
    if kind == "exact":
        norm = squad_normalize(answer)
        targets = [squad_normalize(str(v)) for v in values]
        return _combine([bool(t) and t == norm for t in targets], mode), ""
    if kind == "fuzzy":
        a = squad_normalize(answer)
        hits = [(a == t) if not (a and t) else (a in t or t in a) for t in map(squad_normalize, map(str, values))]
        return _combine(hits, mode), ""
    if kind == "regex":
        flags = re.I if check.get("ignore_case") else 0
        try:
            return _combine([re.search(str(v), answer, flags) is not None for v in values], mode), ""
        except re.error as exc:
            raise CheckError(f"regex {values!r} does not compile: {exc}") from exc
    if kind == "choice":
        picked = chosen_letters(answer)
        want = frozenset(str(v).strip().upper() for v in values)
        return picked == want, f"picked {''.join(sorted(picked)) if picked else 'nothing'}"
    if kind == "json":
        ok, parsed = parse_json(answer, contains=bool(check.get("contains")))
        if not ok:
            return False, "not JSON"
        return (not values or any(parsed == v for v in values)), ""
    raise CheckError(f"unknown check type {kind!r}")


def judge_point(check: Mapping[str, Any]) -> str:
    """The required point a ``judge`` check asks the checklist judge about."""
    criterion = str(check.get("criterion") or "").strip()
    reference = str(check.get("reference") or "").strip()
    if reference and criterion:
        return f"{criterion} (reference answer: {reference})"
    if reference:
        return f"The answer agrees with this reference answer, with no contradiction: {reference}"
    if criterion:
        return criterion
    raise CheckError("a judge check needs a criterion or a reference")


def grade_check(check: Mapping[str, Any], question: str, answer: str, ask: Optional[Ask]) -> CheckResult:
    kind = str(check.get("type", ""))
    weight = float(check.get("weight", 1.0))
    if kind == UNSUPPORTED:
        return CheckResult(kind, None, None, weight, str(check.get("native", "")))
    if kind in JUDGED:
        if ask is None:
            return CheckResult(kind, None, None, weight, "not judged: no judge model")
        from dokimi.eval.checklist import check_answer

        verdict = check_answer(ask, question, answer, [judge_point(check)])[0]
        score = verdict.score
        if score is not None and check.get("negate"):
            score = 1.0 - score
        return CheckResult(kind, None if score is None else score == 1.0, score, weight)
    passed, detail = _deterministic(check, answer)
    if check.get("negate"):
        passed = not passed
    return CheckResult(kind, passed, 1.0 if passed else 0.0, weight, detail)


def validate_grading(grading: Any) -> list[str]:
    """Problems with a grading block, empty when it can be graded."""
    if not isinstance(grading, Mapping):
        return ["grading is not an object"]
    checks = grading.get("checks")
    if not isinstance(checks, list) or not checks:
        return ["grading has no checks"]
    problems = [] if _number_or_none(grading.get("threshold")) else ["threshold is not a number"]
    for i, c in enumerate(checks):
        kind = c.get("type") if isinstance(c, Mapping) else None
        if kind not in KNOWN:
            problems.append(f"check {i}: unknown type {kind!r}")
            continue
        # Everything grade_check would raise on, so a bad block stops the run before any judge call.
        problems.extend(f"check {i}: {p}" for p in _check_problems(kind, c))
    return problems


def _number_or_none(value: Any) -> bool:
    return value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value))


def _check_problems(kind: str, c: Mapping[str, Any]) -> list[str]:
    out = [] if _number_or_none(c.get("weight")) else ["weight is not a number"]
    if kind in DETERMINISTIC and kind != "json" and not _values(c):
        out.append(f"{kind} has no values")
    if kind in DETERMINISTIC and str(c.get("mode", "any")) not in ("any", "all"):
        out.append(f"mode must be 'any' or 'all', not {c.get('mode')!r}")
    if kind == "match" and str(c.get("location", "end")) not in _LOCATIONS:
        out.append(f"match location must be one of {', '.join(_LOCATIONS)}, not {c.get('location')!r}")
    if kind == "regex":
        for v in _values(c):
            try:
                re.compile(str(v))
            except re.error as exc:
                out.append(f"regex {v!r} does not compile: {exc}")
    if kind == "judge" and not (str(c.get("criterion") or "").strip() or str(c.get("reference") or "").strip()):
        out.append("a judge check needs a criterion or a reference")
    return out


def grade(grading: Mapping[str, Any], question: str, answer: Optional[str], ask: Optional[Ask] = None) -> Grade:
    """Every check of ``grading`` on ``answer``.

    A missing answer (the answer call failed) leaves every check unscored rather than failed:
    a provider error is not the model's wrong answer, and the grade's score is then ``None``.
    """
    threshold = grading.get("threshold")
    out = Grade(threshold=float(threshold) if threshold is not None else None)
    for check in grading.get("checks", []):
        if answer is None:
            out.checks.append(CheckResult(str(check.get("type")), None, None,
                                          float(check.get("weight", 1.0)), "no answer"))
            continue
        out.checks.append(grade_check(check, question, answer or "", ask))
    return out
