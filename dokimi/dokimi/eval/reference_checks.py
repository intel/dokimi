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
``regex``  the pattern is found                          promptfoo ``regex``
``choice`` the ``ANSWER: <letters>`` line names exactly   Inspect ``choice`` (with
           the target letters                            ``multiple_choice``)
``json``   the answer parses as JSON (equal to a value,  OpenAI ``JsonMatch``, promptfoo
           when values are given)                        ``is-json``/``contains-json``
``identifiers`` every identifier the answer names    the grading ladder's veto
           (``pattern``, long flags by default, inside   (:doc:`grading-tiers`)
           ``within`` matches) is in ``known``, a list
           taken from a pinned source (``--help``)
``judge``  the judge says the answer meets ``criterion``  Inspect ``model_graded_*``, OpenAI
           (or agrees with ``reference``)                ``ModelBasedClassify``, promptfoo
                                                         ``llm-rubric``/``factuality``,
                                                         skill-creator expectations
========== ============================================ ==========================================

``mode: all`` makes a multi-value check need every value (promptfoo ``contains-all``); ``negate``
inverts it (promptfoo ``not-*``). A check of type ``unsupported`` records what an importer could
not translate (code asserts, embeddings, cost); it is reported and never counted. A ``regex``
check that runs past :data:`REGEX_TIMEOUT` is left unscored the same way.

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
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

import regex

from dokimi.measure import as_float

#: Seconds a ``regex`` check may run before it is left unscored (``re`` has no timeout).
REGEX_TIMEOUT = 1.0

#: Check types this module grades without a model.
DETERMINISTIC = frozenset({"match", "exact", "fuzzy", "regex", "choice", "json", "identifiers"})
#: What a check may do to a verdict that another grader (the novelty judge) gave: see :func:`check_role`.
ROLES = ("evidence", "veto", "sufficient")
#: How objective a check is: decided by a program, by a pinned external source, or by a model.
TIERS = ("verifiable", "grounded", "judge")
#: The identifiers check's default: long command-line options (``--stdio``).
LONG_FLAG = r"(?<![\w-])(--[A-Za-z][\w-]*)"
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
    #: Set when the check could not be graded because a call failed (the judge), not because it has no verdict.
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "passed": self.passed, "score": self.score}
        if self.weight != 1.0:
            out["weight"] = self.weight
        if self.detail:
            out["detail"] = self.detail
        if self.error is not None:
            out["error"] = self.error
        return out


@dataclass
class Grade:
    """One answer graded against one question's checks.

    An ``error`` (the answer call failed, or a judge call did) means the answer has no grade: no
    score and no pass, whatever the other checks said. It is counted as an error, not as unscored.
    """

    checks: list[CheckResult] = field(default_factory=list)
    threshold: Optional[float] = None
    error: Optional[str] = None

    @property
    def counted(self) -> list[CheckResult]:
        return [c for c in self.checks if c.type != UNSUPPORTED and c.score is not None and c.weight > 0]

    @property
    def unjudged(self) -> list[CheckResult]:
        """Judged checks with no verdict (no judge model, or the judge call failed)."""
        return [c for c in self.checks if c.type in JUDGED and c.score is None]

    @property
    def unrun(self) -> list[CheckResult]:
        """Deterministic checks left without a result (a regex past its timeout)."""
        return [c for c in self.checks if c.type not in JUDGED | {UNSUPPORTED} and c.score is None]

    @property
    def score(self) -> Optional[float]:
        if self.error is not None:
            return None
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
        out: dict[str, Any] = {
            "aggregate": None if score is None else round(100 * score, 1),
            "passed": self.passed,
            "checks": [c.as_dict() for c in self.checks],
        }
        if self.error is not None:
            out["error"] = self.error
        return out


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

#: ``parse_json(contains=True)`` tries ``raw_decode`` from at most this many ``{`` or ``[``:
#: each try on deeply nested text parses to the decoder's depth limit.
CONTAINS_JSON_MAX_STARTS = 1000

#: ...and the tries that fail may scan at most this many characters in all (each from its start to
#: where it failed): a text of many brackets and a long tail would otherwise cost starts x length.
CONTAINS_JSON_MAX_SCAN = 1_000_000

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n(.*?)\n\s*```\s*$", re.S)


def parse_json(answer: str, *, contains: bool = False) -> tuple[bool, Any]:
    """``(parsed, value)``: the answer as JSON (one fenced block is unwrapped).

    With ``contains`` the first JSON object or array anywhere in the text counts
    (promptfoo ``contains-json``). The scan gives up after :data:`CONTAINS_JSON_MAX_STARTS`
    brackets, or once the tries that failed have scanned :data:`CONTAINS_JSON_MAX_SCAN`
    characters in all (reaching it spends it; one try may cross it), and then reads the text as not JSON. The first parse of the whole text is
    not counted, only the tries that start at a bracket.
    """
    text = answer.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        return True, json.loads(text)
    except (ValueError, RecursionError):
        pass
    if contains:
        decoder = json.JSONDecoder()
        tried = 0
        scanned = 0
        for i, ch in enumerate(text):
            if ch in "{[":
                if tried == CONTAINS_JSON_MAX_STARTS or scanned >= CONTAINS_JSON_MAX_SCAN:
                    break
                tried += 1
                try:
                    return True, decoder.raw_decode(text, i)[0]
                except json.JSONDecodeError as err:
                    # An unterminated string reports its opening quote, but read to the end.
                    end = len(text) if err.msg.startswith("Unterminated string") else err.pos
                    scanned += max(end - i, 0)
                except (ValueError, RecursionError):
                    # No position (an overlong integer, the depth limit): charge the whole tail.
                    scanned += len(text) - i
    return False, None


# --- one check ---------------------------------------------------------------------------------


def check_values(check: Mapping[str, Any]) -> list[Any]:
    raw = check.get("values", check.get("value"))
    if raw is None:
        return []
    return list(raw) if isinstance(raw, (list, tuple)) else [raw]


def _combine(hits: Sequence[bool], mode: str) -> bool:
    if mode not in ("any", "all"):
        raise CheckError(f"check mode must be 'any' or 'all', not {mode!r}")
    return bool(hits) and (all(hits) if mode == "all" else any(hits))


def _regex(check: Mapping[str, Any], values: list[Any], answer: str, mode: str) -> tuple[Optional[bool], str]:
    """The ``regex`` check, ``None`` when its patterns run past :data:`REGEX_TIMEOUT` together."""
    flags = regex.I if check.get("ignore_case") else 0
    deadline = time.monotonic() + REGEX_TIMEOUT
    hits: list[bool] = []
    try:
        for v in values:
            hits.append(regex.search(str(v), answer, flags, timeout=max(deadline - time.monotonic(), 0.001)) is not None)
            # Stop once the mode is settled, so a later pattern past the limit cannot unscore the check.
            if (True in hits) if mode == "any" else (False in hits):
                break
        return _combine(hits, mode), ""
    except TimeoutError:
        return None, f"not scored: regex ran past {REGEX_TIMEOUT:g}s"
    except regex.error as exc:
        raise CheckError(f"regex {values!r} does not compile: {exc}") from exc


def _deterministic(check: Mapping[str, Any], answer: str) -> tuple[Optional[bool], str]:
    kind = check["type"]
    values = check_values(check)
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
        return _regex(check, values, answer, mode)
    if kind == "choice":
        picked = chosen_letters(answer)
        want = frozenset(str(v).strip().upper() for v in values)
        return picked == want, f"picked {''.join(sorted(picked)) if picked else 'nothing'}"
    if kind == "json":
        ok, parsed = parse_json(answer, contains=bool(check.get("contains")))
        if not ok:
            return False, "not JSON"
        return (not values or any(parsed == v for v in values)), ""
    if kind == "identifiers":
        return _identifiers(check, answer)
    raise CheckError(f"unknown check type {kind!r}")


def _identifiers(check: Mapping[str, Any], answer: str) -> tuple[Optional[bool], str]:
    """Every identifier the answer names (inside ``within``) is one the source lists in ``known``.

    The tier-1/2 veto of the grading ladder: an answer that names a ``perf report --stdin`` flag
    the tool's own ``--help`` does not list is wrong whatever a judge reads into it. An answer
    that names none passes: the check only rules names out, it never asks for one.
    """
    unknown, scoped = identifier_names(check, answer)
    if unknown is None:
        return None, f"not scored: identifiers ran past {REGEX_TIMEOUT:g}s"
    if unknown:  # the detail names ten at most; identifier_names has them all
        return False, "not in source: " + ", ".join(unknown[:10])
    return True, "" if scoped else "nothing to check"


def identifier_names(check: Mapping[str, Any], answer: str) -> tuple[Optional[list[str]], bool]:
    """Every identifier the answer names that ``known`` does not list, in order, and whether any was read.

    ``None`` when a scan ran past its time bound (the check is then not scored). The second value
    is False when the answer names no scope to read (a ``within`` that matches nothing).
    """
    known = {str(k) for k in check.get("known") or ()}
    pattern = str(check.get("pattern") or LONG_FLAG)
    within = check.get("within")
    # Both scans share one deadline, as the regex check does: a pathological pattern is unscored.
    deadline = time.monotonic() + REGEX_TIMEOUT
    try:
        scopes = scope_pairs(str(within), answer, timeout=_left(deadline)) if within else [(answer, "")]
        unknown: list[str] = []
        for scope, rest in scopes:
            for name in scope_options(pattern, scope, timeout=_left(deadline), negated=bool(within), rest=rest):
                if name in known or (check.get("negations", False) and name.startswith("--no-")
                                     and "--" + name[5:] in known):
                    continue
                if name not in unknown:
                    unknown.append(name)
    except TimeoutError:
        return None, False
    except regex.error as exc:
        raise CheckError(f"identifiers pattern does not compile: {exc}") from exc
    return unknown, bool(scopes)


def scope_pairs(within: str, answer: str, *, timeout: float) -> list[tuple[str, str]]:
    """The ``within`` scopes of ``answer`` that no negation before them makes no claim, each with the answer's text after it.

    A mention the answer negates or warns against (``do not use --x``) is no claim to veto. The text
    after a scope is kept too: a denial may follow a code span that ends the scope ("`--x` does not exist").
    """
    return [(m.group(0), answer[m.end():]) for m in regex.finditer(within, answer, timeout=timeout)
            if not _negated_before(answer, m.start())]


_NEGATED = re.compile(r"(?i)\bnot\b|n['’]t\b|\bnever\b|\bno\b|\bavoid\w*|\bwithout\b|\binstead of\b|\brather than\b")
#: An existence denial right after a flag, in its code span or not, denies it: only these two phrases, on one
#: line; other words after a flag leave its claim standing.
_DENIED_AFTER = re.compile(
    r"""[ \t`'"*)]*(?:does(?:[ \t]+not|n['’]t)[ \t]+exist|(?:is[ \t]+not|isn['’]t)[ \t]+an[ \t]+option)(?![\w-])""",
    re.IGNORECASE)
#: An option with an assigned value (``--input=--stdin``): the value is a file name, not an option.
_ASSIGNED = re.compile(r"""(?<![\w-])(--[A-Za-z][\w-]*)=(?:"[^"\n]*"|'[^'\n]*'|[^\s,;|&]+)""")
#: A standalone ``--`` ends the options of a command: the words after it, up to the end of the
#: clause, are operands (``perf report --stdio -- --foo`` names no option of ``perf report``).
_OPERANDS = re.compile(r"(?<!\S)--(?!\S)(?:(?![.,](?:\s|$))[^\n;!?`#])*")
#: A word a flag takes after a space (``--sort no``) is its value: it negates no clause and, with the
#: default pattern, names no option (``--input "no --stdin"``; a quoted ``--x`` still does).
_VALUE_WORD = re.compile(
    r"((?<![\w-])--[A-Za-z][\w-]*\s+)"
    r"(?!(?:not|never|without|rather|instead|avoid\w*|and|but|then|while)\b|[\w.-]*n['’]t\b)"
    r"(?:\"(?!-)[^\"\n]*\"(?!\w)|'(?!-)[^'\n]*'(?!\w)|[\w./][\w./-]*)",
    re.IGNORECASE)


def _negated_before(answer: str, start: int) -> bool:
    """Whether the clause just before ``start`` negates or warns against what follows it."""
    clause = re.split(r"[.;!?,\n]|\b(?:and|but|then|while)\b", answer[:start], flags=re.I)[-1]
    # "not only" and "no problem" affirm: they are no negation of what follows.
    clause = re.sub(r"(?i)\bnot\s+only\b|\bno\s+problem\b", " ", clause)
    clause = re.sub(r"(?<![\w-])--?[A-Za-z][\w-]*", " ", clause)  # flags are no words
    return _NEGATED.search(clause) is not None


def scope_options(pattern: str, scope: str, *, timeout: float, negated: bool, rest: str = "") -> list[str]:
    """The option names ``pattern`` finds in ``scope``, in order.

    With the default pattern, an assigned value names no option (``--input=--stdin`` names
    ``--input`` only), and so does the value a flag takes (``--input "no --stdin"`` names ``--input``);
    a custom pattern reads the text as written, its groups may take a value. With
    ``negated``, a mention that the clause before it negates names none (``run --stdio, not
    --stdin``), and so does one that ``does not exist`` or ``is not an option`` denies right after it:
    a command's scope is judged mention by mention, not only by the words before it.
    Both the veto and the key verification read a claim through this one scan.
    """
    def blank(m: regex.Match) -> str:  # keeps group 1, blanks the rest: same length as the scope
        return m.group(1) + " " * (m.end() - m.start() - len(m.group(1)))

    text = scope
    if pattern == LONG_FLAG:  # operands after "--" name no option; the same length, so offsets hold
        text = _OPERANDS.sub(lambda m: " " * len(m.group()), scope)
    if pattern == LONG_FLAG:  # the value of --input=--stdin is a file name, not an option
        text = _ASSIGNED.sub(blank, text)
    # The clause of a mention is read past the value a flag takes (``--sort no --stdin``).
    clause = _VALUE_WORD.sub(blank, text)
    if pattern == LONG_FLAG:  # a flag's value (a file name, quoted or not) names no option either
        text = clause
    names = []
    for m in regex.finditer(pattern, text, timeout=timeout):
        # The denial is read in the text as written (the clause above blanks the words after a flag), and past
        # the scope when a code span ends it: ``rest`` is the answer after the scope.
        if negated and (_negated_before(clause, m.start()) or _DENIED_AFTER.match(scope + rest, m.end())):
            continue
        # The first group is unset when another alternative of the pattern matched.
        name = (m.group(1) if m.groups() and m.group(1) is not None else m.group(0))
        names.append(name)
    return names


def _left(deadline: float) -> float:
    """Seconds left before ``deadline``, never zero (a scan with no time left still times out)."""
    return max(deadline - time.monotonic(), 0.001)


def check_role(check: Mapping[str, Any]) -> str:
    """``role`` of a check, ``evidence`` when it sets none."""
    return str(check.get("role") or "evidence")


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
        from dokimi.eval.checklist import JudgeFailed, check_answer

        try:
            verdict = check_answer(ask, question, answer, [judge_point(check)], strict=True)[0]
        except JudgeFailed as exc:
            return CheckResult(kind, None, None, weight, "judge failed", error=f"judge: {exc}")
        score = verdict.score
        if score is not None and check.get("negate"):
            score = 1.0 - score
        return CheckResult(kind, None if score is None else score == 1.0, score, weight)
    passed, detail = _deterministic(check, answer)
    if passed is None:
        return CheckResult(kind, None, None, weight, detail)
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
        # A non-string type (a list, say) is unhashable: test the type before the membership check.
        if not isinstance(kind, str) or kind not in KNOWN:
            problems.append(f"check {i}: unknown type {kind!r}")
            continue
        # Everything grade_check would raise on, so a bad block stops the run before any judge call.
        problems.extend(f"check {i}: {p}" for p in _check_problems(kind, c))
    return problems


def _number_or_none(value: Any) -> bool:
    return value is None or as_float(value) is not None


def blank_value(kind: str, c: Mapping[str, Any], value: Any) -> bool:
    """Whether a value is found in every answer, or never met, once the check has normalised it."""
    text = str(value)
    if kind == "regex":
        return text == ""  # the empty pattern matches anywhere; " " is a real "contains a space" check
    if kind == "match" and c.get("ignore_punctuation", True):
        text = _strip_punct(text)  # "..." is then "", found in every answer
    if kind == "match" and not c.get("strip", True):
        return text == ""  # unstripped, " " is a real "contains a space" check
    return not text.strip()


def _ladder_problems(c: Mapping[str, Any]) -> list[str]:
    """A ``role`` or ``tier`` outside its vocabulary."""
    out = []
    for key, allowed in (("role", ROLES), ("tier", TIERS)):
        if c.get(key) is not None and c.get(key) not in allowed:
            out.append(f"{key} must be one of {', '.join(allowed)}, not {c.get(key)!r}")
    return out


def _identifiers_problems(c: Mapping[str, Any]) -> list[str]:
    """An ``identifiers`` check without names, a ``sufficient`` role, or a pattern that does not compile."""
    known = c.get("known")
    names_ok = isinstance(known, list) and known and all(isinstance(k, str) and k for k in known)
    out = [] if names_ok else ["identifiers needs `known`: a non-empty list of names"]
    # It only rules names out: an answer that names none passes, so it cannot decide a verdict.
    if check_role(c) == "sufficient":
        out.append("identifiers cannot be sufficient: it only rules names out, and an answer naming none passes")
    for key in ("pattern", "within"):  # the scan runs them with the regex module, not re
        if c.get(key) is not None:
            try:
                regex.compile(str(c[key]))
            except regex.error as exc:
                out.append(f"identifiers {key} {c[key]!r} does not compile: {exc}")
    return out


def _values_problems(kind: str, c: Mapping[str, Any]) -> list[str]:
    """A value check with no values, or a blank one."""
    values = check_values(c)
    if not values:
        return [f"{kind} has no values"]
    # A blank value is found in every answer (match, regex), so it would pass everything.
    return [f"{kind} has a blank value"] if any(blank_value(kind, c, v) for v in values) else []


def _check_problems(kind: str, c: Mapping[str, Any]) -> list[str]:
    # An absent weight is 1.0; an explicit null is not a number, and grade_check would raise on it.
    weight = c.get("weight", 1.0)
    out = ["weight is not a number"] if as_float(weight) is None else (
        ["weight must not be negative"] if weight < 0 else [])
    out += _ladder_problems(c)
    if kind == "identifiers":
        out += _identifiers_problems(c)
    if kind in DETERMINISTIC and kind not in ("json", "identifiers"):
        out += _values_problems(kind, c)
    if kind in DETERMINISTIC and str(c.get("mode", "any")) not in ("any", "all"):
        out.append(f"mode must be 'any' or 'all', not {c.get('mode')!r}")
    if kind == "match" and str(c.get("location", "end")) not in _LOCATIONS:
        out.append(f"match location must be one of {', '.join(_LOCATIONS)}, not {c.get('location')!r}")
    if kind == "regex":
        for v in check_values(c):
            try:
                regex.compile(str(v))
            except regex.error as exc:
                out.append(f"regex {v!r} does not compile: {exc}")
    if kind == "judge" and not (str(c.get("criterion") or "").strip() or str(c.get("reference") or "").strip()):
        out.append("a judge check needs a criterion or a reference")
    return out


def grade(grading: Mapping[str, Any], question: str, answer: Optional[str], ask: Optional[Ask] = None,
          *, answer_error: Optional[str] = None) -> Grade:
    """Every check of ``grading`` on ``answer``.

    A missing answer leaves every check unscored rather than failed: a provider error is not the
    model's wrong answer. ``answer_error`` names why there is none; it makes the grade an error.
    A judge check whose call failed makes the grade an error too, so no pass is decided by the
    checks that did run.
    """
    threshold = grading.get("threshold")
    out = Grade(threshold=float(threshold) if threshold is not None else None, error=answer_error)
    judge_failed = False
    for check in grading.get("checks", []):
        kind = str(check.get("type"))
        if answer is None:
            out.checks.append(CheckResult(kind, None, None,
                                          float(check.get("weight", 1.0)), "no answer"))
            continue
        if judge_failed and kind in JUDGED:
            # The grade is already an error, so a further judge call could not change it: skip the paid call.
            out.checks.append(CheckResult(kind, None, None, float(check.get("weight", 1.0)),
                                          "not judged: an earlier judge check failed"))
            continue
        result = grade_check(check, question, answer or "", ask)
        out.checks.append(result)
        if out.error is None and result.error is not None:
            out.error = result.error
        judge_failed = judge_failed or (kind in JUDGED and result.error is not None)
    return out
