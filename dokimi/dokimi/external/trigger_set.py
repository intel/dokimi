# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Build a skill-selection (trigger) query set from golden question sets.

A trigger set asks "does the agent load the right skill?", not "is the answer
right?", so it is kept separate from the answer-quality question set it is
derived from. Each query carries ``expected_skill`` (a name, or ``None`` when no
skill in the catalog should load) and a lint record:

- ``names_skill`` — the text names a catalog skill (the agent is told the answer);
- ``echo_terms`` — distinctive words of the expected skill's description (words
  no rival description uses) that the text repeats; echo inflates selection;
- ``exam_framing`` — "Scenario:" / "Describe how" style, which a coding agent
  answers from memory instead of loading a skill;
- ``lexical_rank`` — where word overlap alone ranks the expected skill (1 = the
  query is lexically easy, and a router needs no understanding to get it right).

The optional rewrite turns exam-style questions into first-person requests. The
rewriting model sees only the question text, never the catalog, so it cannot
copy a description into the query; lint runs again on the rewritten text.

Writers target the formats external selection tools read: google/skill-reach
(``QuerySet`` JSON), anthropics/skills skill-creator (``[{query, should_trigger}]``
for one target skill), and this repo's ``questions.v1`` (for the
``skill-catalog:`` arm).
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from dokimi.utils import question_id
from dokimi.skills import Skill

ExpectedSkill = Union[str, List[str], None]


class LabelUnresolved(LookupError):
    """An ``@field.path`` label rule found no such field on the question.

    Kept apart from a field that is present and empty: that one means "no skill
    should load", while a missing path is usually a typo, and reading it as a
    negative would score "never load a skill" as a perfect run.
    """

_EXAM = re.compile(
    r"^\s*(scenario|end-to-end task|task)\s*:|\bdescribe how\b|\bwhat does the (documentation|docs?)\b"
    r"|\baccording to the (documentation|docs)\b|\bin the [\w.+-]+ (docs|documentation)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z][a-z0-9_+-]{2,}")
_STOP = frozenset(
    "the and for with that this from when use your you are not but can its into any all "
    "one how what why does which their them then than via also only more most such over "
    "should when where there have has been need needs into onto out about after before "
    "every each other some same".split()
)

REWRITE_PROMPT = (
    "Rewrite the following benchmark question as the message a developer would actually "
    "type to a coding agent working in their own repository: first person, a direct "
    "request or question, keeping every technical detail (numbers, commands, API names, "
    "symptoms). Drop exam framing such as 'Scenario:', 'End-to-end task:', 'Describe how'. "
    "Do not mention skills, tools, catalogs or documentation lookups, and do not add facts "
    "that are not in the original. Output only the message.\n\nQuestion:\n"
)


def _words(text: str) -> set:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


@dataclass
class TriggerQuery:
    """One labelled selection probe."""

    id: str
    text: str
    expected_skill: ExpectedSkill
    source_text: Optional[str] = None
    lint: Dict[str, Any] = field(default_factory=dict)
    labelled: bool = True

    @property
    def acceptable(self) -> List[str]:
        e = self.expected_skill
        return list(e) if isinstance(e, list) else ([e] if e else [])


def _parse_mapping(text: str, rule: str) -> Any:
    """Parse a field stored as a string: JSON first (``true``/``null``), then a Python literal."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError) as exc:
        raise LabelUnresolved(rule) from exc


def resolve_label(question: Dict[str, Any], rule: str) -> ExpectedSkill:
    """Label a question by *rule*: a skill name, ``none``, or ``@<dotted.path>``.

    ``@metadata.skill_under_test`` reads a field of the question; a ``metadata``
    stored as a Python-literal string (as some golden sets do) is parsed safely.
    A field that is present but empty labels the question ``None``; a path that
    does not resolve raises :class:`LabelUnresolved`.
    """
    if rule == "none":
        return None
    if not rule.startswith("@"):
        return rule
    value: Any = question
    for part in rule[1:].split("."):
        if isinstance(value, str):
            value = _parse_mapping(value, rule)
        if not isinstance(value, dict) or part not in value:
            raise LabelUnresolved(rule)
        value = value[part]
    if isinstance(value, list):
        return [str(v) for v in value] or None
    return str(value) if value else None


def lint_query(query: TriggerQuery, catalog: Sequence[Skill]) -> Dict[str, Any]:
    """Leakage and framing checks for one query against the catalog."""
    text = query.text.lower()
    names = [s.name for s in catalog]
    # Whole words only: a short name such as "git" must not match "digital".
    named = [n for n in names
             if any(re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", text)
                    for v in {n.lower(), n.lower().replace("-", " ")})]
    words = _words(query.text)
    echo: List[str] = []
    by_name = {s.name: s for s in catalog}
    for target in query.acceptable:
        skill = by_name.get(target)
        if skill is None:
            continue
        rivals = set().union(*(_words(s.description) for s in catalog if s.name != target))
        echo += sorted((_words(skill.description) - rivals) & words)
    overlap = {s.name: len(words & _words(f"{s.name.replace('-', ' ')} {s.description}")) for s in catalog}
    # Ties rank pessimistically: rank 1 means word overlap alone picks the skill,
    # not that it ties (or, at zero overlap, merely comes first in catalog order).
    # Only skills outside the acceptable set are rivals.
    acceptable = set(query.acceptable)
    rank = min(
        (1 + sum(v >= overlap[t] for n, v in overlap.items() if n not in acceptable)
         for t in query.acceptable if t in overlap),
        default=None,
    )
    return {
        "names_skill": named,
        "echo_terms": sorted(set(echo)),
        "exam_framing": bool(_EXAM.search(query.text)),
        "lexical_rank": rank,
        "unknown_expected": [t for t in query.acceptable if t not in by_name],
    }


def build_trigger_set(
    sources: Sequence[tuple],
    catalog: Sequence[Skill],
    rewrite: Optional[Callable[[str], str]] = None,
) -> List[TriggerQuery]:
    """Label, optionally rewrite, and lint questions.

    *sources* is ``[(questions, rule), ...]`` — a question list and the
    :func:`resolve_label` rule that labels it. *rewrite* maps question text to a
    new query text and is given nothing but that text.
    """
    out: List[TriggerQuery] = []
    seen: set = set()
    for questions, rule in sources:
        for q in questions:
            raw = question_id(q)
            qid = str(f"q{len(out) + 1}" if raw is None else raw)
            if qid in seen:
                raise ValueError(f"duplicate question id across sources: {qid}")
            seen.add(qid)
            text = q.get("text") or q.get("question") or q.get("prompt") or q.get("question_text") or ""
            if not isinstance(text, str) or not text.strip():
                raise ValueError(
                    f"question {qid} has no text (looked in text, question, prompt, question_text)")
            try:
                tq = TriggerQuery(id=qid, text=text, expected_skill=resolve_label(q, rule))
            except LabelUnresolved:
                tq = TriggerQuery(id=qid, text=text, expected_skill=None, labelled=False)
            if rewrite is not None:
                tq.source_text, tq.text = text, rewrite(text).strip()
                if not tq.text:
                    raise ValueError(f"rewrite returned empty text for question {qid}")
            tq.lint = lint_query(tq, catalog)
            tq.lint["label_unresolved"] = not tq.labelled
            out.append(tq)
    return out


def lint_summary(queries: Sequence[TriggerQuery]) -> Dict[str, Any]:
    """Counts a reviewer reads before trusting a trigger set."""
    pos = [q for q in queries if q.acceptable]
    unresolved = sum(not q.labelled for q in queries)
    return {
        "n": len(queries),
        "n_positive": len(pos),
        "n_negative": len(queries) - len(pos) - unresolved,
        "label_unresolved": unresolved,
        "names_skill": sum(bool(q.lint.get("names_skill")) for q in queries),
        "echo": sum(bool(q.lint.get("echo_terms")) for q in pos),
        "exam_framing": sum(bool(q.lint.get("exam_framing")) for q in queries),
        "lexically_easy": sum(q.lint.get("lexical_rank") == 1 for q in pos),
        "unknown_expected": sorted({t for q in queries for t in q.lint.get("unknown_expected", [])}),
    }


def labelled(queries: Sequence[TriggerQuery]) -> List[TriggerQuery]:
    """Queries with a resolved label; writers for external tools drop the rest."""
    return [q for q in queries if q.labelled]


def to_reach(queries: Sequence[TriggerQuery], catalog_id: str = "") -> Dict[str, Any]:
    """google/skill-reach ``QuerySet``; extra acceptable names become ``acceptable_skills``."""
    items = []
    for q in labelled(queries):
        acc = q.acceptable
        item: Dict[str, Any] = {"id": q.id, "text": q.text, "expected_skill": acc[0] if acc else None}
        if not acc:
            item["kind"] = "out_of_scope"
        if len(acc) > 1:
            item["acceptable_skills"] = acc[1:]
        items.append(item)
    return {"catalog_id": catalog_id, "queries": items}


def to_skill_creator(queries: Sequence[TriggerQuery], target: str) -> List[Dict[str, Any]]:
    """anthropics/skills skill-creator eval set for one *target* skill."""
    return [{"query": q.text, "should_trigger": target in q.acceptable} for q in labelled(queries)]


def to_questions(queries: Sequence[TriggerQuery]) -> Dict[str, Any]:
    """``questions.v1`` carrying ``expected_skill``, for the ``skill-catalog:`` arm."""
    rows = []
    for q in queries:
        row: Dict[str, Any] = {"id": q.id, "text": q.text, "trigger_lint": q.lint}
        if q.labelled:  # absent = unlabelled, which selection scoring skips
            row["expected_skill"] = q.expected_skill
        if q.source_text is not None:
            row["source_text"] = q.source_text
        rows.append(row)
    return {"schema_version": "questions.v1", "questions": rows}
