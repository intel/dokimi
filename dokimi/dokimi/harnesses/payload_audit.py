# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Leak audit: does what an agent is sent carry dokimi's own artifacts?

A measured run is meant to reproduce what a user's agent sees. Anything dokimi adds
that names the experiment (an arm name, the ``expected_skill`` key, a question id,
judge or rubric text, a task path that says what is graded) can change what the model
does, and makes the number measure dokimi rather than the harness.

:func:`audit_payload` walks a payload (a captured request body, an argv, an env map:
any JSON-like value) and reports every marker it finds. Text the operator supplied
on purpose (the query, the skills' own descriptions and bodies) is passed as
``allowed`` and blanked out before matching: a skill whose description says
"baseline" is content, not a leak. What is left is what dokimi wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, List, Optional, Sequence, Tuple

#: Words and keys that name an experiment rather than a user's task. Matched whole-word,
#: case-insensitive, after the allowed texts are blanked.
DEFAULT_MARKERS: Tuple[str, ...] = (
    "dokimi", "expected_skill", "acceptable_skills", "should_trigger", "trigger_set",
    "skill_catalog", "skill-catalog", "skill_selection", "treatment", "baseline",
    "with_docs", "without_docs", "judge", "rubric", "golden", "grader", "verifier",
    "reward", "oracle", "eval", "evals", "evaluation", "benchmark", "arm_kind",
    "abskill", "test_outputs",
)


@dataclass(frozen=True)
class Leak:
    """One marker found: where (a JSON path), which marker, and the text around it."""

    path: str
    marker: str
    excerpt: str

    def as_dict(self) -> dict:
        return {"path": self.path, "marker": self.marker, "excerpt": self.excerpt}


def markers_for(*, question_ids: Iterable[str] = (), arm_names: Iterable[str] = (),
                paths: Iterable[str] = (), extra: Iterable[str] = (),
                base: Sequence[str] = DEFAULT_MARKERS) -> List[str]:
    """The default markers plus this run's own identifiers.

    *question_ids* and *arm_names* come from the run; *paths* are host paths that
    reveal intent (a task directory, an output dir). Empty values are dropped.
    """
    out = list(base)
    for group in (question_ids, arm_names, paths, extra):
        out += [str(v) for v in group if str(v).strip()]
    return out


def _strings(value: Any, path: str = "$") -> Iterator[Tuple[str, str]]:
    """Every string in *value* (keys included) with its JSON path."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield f"{path}.{k}#key", str(k)
            yield from _strings(v, f"{path}.{k}")
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")


def _pattern(marker: str) -> "re.Pattern[str]":
    # Identifier-ish markers match between non-alphanumerics, so `dokimi-run-x` and
    # `eval_queries` are caught while `evaluate` is not; anything else is a substring.
    if re.fullmatch(r"[\w-]+", marker):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(marker)}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(re.escape(marker), re.IGNORECASE)


def _blank(text: str, allowed: Sequence[str]) -> str:
    for a in allowed:
        if a and a in text:
            text = text.replace(a, " " * len(a))
    return text


def audit_payload(payload: Any, markers: Optional[Iterable[str]] = None,
                  allowed: Iterable[str] = ()) -> List[Leak]:
    """Markers found in *payload* outside the *allowed* texts, one :class:`Leak` each.

    *allowed* is longest-first blanked, so a skill body containing its description
    blanks as a whole. An empty result means the payload carries none of the markers.
    """
    pats = [(m, _pattern(m)) for m in (DEFAULT_MARKERS if markers is None else markers) if m]
    keep = sorted({a for a in allowed if a and a.strip()}, key=len, reverse=True)
    leaks: List[Leak] = []
    for path, text in _strings(payload):
        visible = _blank(text, keep)
        for marker, pat in pats:
            for m in pat.finditer(visible):
                lo, hi = max(0, m.start() - 40), min(len(text), m.end() + 40)
                leaks.append(Leak(path, marker, text[lo:hi].replace("\n", " ")))
    return leaks


def added_framing(prompt: str, query: str) -> str:
    """What a harness put in the prompt around the user's *query* (``""`` if verbatim).

    The framing is not a leak by the marker test, but it is an influence: an
    out-of-the-box run sends the query alone.
    """
    if prompt == query:
        return ""
    if query and query in prompt:
        head, _, tail = prompt.partition(query)
        return (head + tail).strip()
    return prompt


def new_leaks(payload: Any, reference: Any, markers: Optional[Iterable[str]] = None,
              allowed: Iterable[str] = ()) -> List[Leak]:
    """Leaks in *payload* that a *reference* capture does not have.

    The reference is the same harness run out of the box (no dokimi in the loop): its
    own system prompt says "judge edge cases" and "golden path", which is the
    harness, not the experiment. A marker counts as new when its excerpt is not
    among the reference's.
    """
    marks = list(DEFAULT_MARKERS if markers is None else markers)
    allowed = list(allowed)
    seen = {(lk.marker, lk.excerpt) for lk in audit_payload(reference, marks, allowed)}
    return [lk for lk in audit_payload(payload, marks, allowed) if (lk.marker, lk.excerpt) not in seen]
