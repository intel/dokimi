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
for one target skill), and this repo's questions artifact (for the
``skill-catalog:`` arm; saved with :func:`~dokimi.questions.artifact.save_questions_artifact`).

Readers take the same three formats back to :class:`TriggerQuery` (the
canonical model), so a set authored for one tool can be linted against a
catalog and run by the ``skill-catalog:`` arm, or re-written for another tool.
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


class TriggerSetInputError(ValueError):
    """The question sources cannot form a trigger set: a duplicate id, a question with no text, or an
    ``@field`` rule that labels none of its source's questions.

    A usage error (exit 2). A rewrite that fails is a runtime error and stays a plain ValueError.
    """


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
    reach: Dict[str, Any] = field(default_factory=dict)  # a read reach row's kind/notes

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
    specs: Optional[Sequence[str]] = None,
) -> List[TriggerQuery]:
    """Label, optionally rewrite, and lint questions.

    *sources* is ``[(questions, rule), ...]`` — a question list and the
    :func:`resolve_label` rule that labels it. *rewrite* maps question text to a
    new query text and is given nothing but that text. When *specs* (one name per
    source) is given, an ``@field`` rule that labels none of its source's questions
    is refused, naming the source; without it such questions stay unlabelled. A ``None``
    entry in *specs* exempts its source: a ``--trigger-set`` file is passed with the
    ``@expected_skill`` rule, and a file with no labels is valid there.
    """
    out: List[TriggerQuery] = []
    seen: set = set()
    for index, (questions, rule) in enumerate(sources):
        first = len(out)
        for q in questions:
            raw = question_id(q)
            qid = str(f"q{len(out) + 1}" if raw is None else raw)
            if qid in seen:
                raise TriggerSetInputError(f"duplicate question id across sources: {qid}")
            seen.add(qid)
            text = q.get("text") or q.get("question") or q.get("prompt") or q.get("question_text") or ""
            if not isinstance(text, str) or not text.strip():
                raise TriggerSetInputError(
                    f"question {qid} has no text (looked in text, question, prompt, question_text)")
            try:
                tq = TriggerQuery(id=qid, text=text, expected_skill=resolve_label(q, rule))
            except LabelUnresolved:
                tq = TriggerQuery(id=qid, text=text, expected_skill=None, labelled=False)
            prior = q.get("source_text")  # a re-read set keeps its pre-rewrite original
            tq.source_text = prior if isinstance(prior, str) else None
            extra = q.get(REACH_FIELDS)
            tq.reach = dict(extra) if isinstance(extra, dict) else {}
            out.append(tq)
        chunk = out[first:]
        if (specs is not None and specs[index] is not None and rule.startswith("@") and chunk
                and not any(tq.labelled for tq in chunk)):
            # A typo in the path, not a set of negatives.
            raise TriggerSetInputError(f"{specs[index]}: {rule} resolves for none of {len(chunk)} questions "
                                       "(none has that field, so the file has no labels)")
    # Every question has passed validation before the first rewrite call, so a refused set costs no model call.
    for tq in out:
        if rewrite is not None:
            original = tq.text
            tq.source_text, tq.text = tq.source_text or original, rewrite(original).strip()
            if not tq.text:
                raise ValueError(f"rewrite returned empty text for question {tq.id}")
        tq.lint = lint_query(tq, catalog)
        tq.lint["label_unresolved"] = not tq.labelled
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
        kind = q.reach.get("kind") if acc else "out_of_scope"
        if kind and not (acc and kind == "out_of_scope"):
            item["kind"] = kind
        if q.reach.get("notes"):
            item["notes"] = q.reach["notes"]
        if len(acc) > 1:
            item["acceptable_skills"] = acc[1:]
        items.append(item)
    return {"catalog_id": catalog_id, "queries": items}


def to_skill_creator(queries: Sequence[TriggerQuery], target: str) -> List[Dict[str, Any]]:
    """anthropics/skills skill-creator eval set for one *target* skill."""
    return [{"query": q.text, "should_trigger": target in q.acceptable} for q in labelled(queries)]


def to_questions(queries: Sequence[TriggerQuery]) -> Dict[str, Any]:
    """A questions payload carrying ``expected_skill``, for the ``skill-catalog:`` arm.

    Unstamped: :func:`~dokimi.questions.artifact.save_questions_artifact` stamps and validates it.
    """
    rows = []
    for q in queries:
        row: Dict[str, Any] = {"id": q.id, "question": q.text, "trigger_lint": q.lint}
        if q.labelled:  # absent = unlabelled, which selection scoring skips
            row["expected_skill"] = q.expected_skill
        if q.source_text is not None:
            row["source_text"] = q.source_text
        rows.append(row)
    return {"questions": rows}


# --- readers -----------------------------------------------------------------

FORMAT_REACH = "reach"
FORMAT_SKILL_CREATOR = "skill-creator"
FORMAT_QUESTIONS = "questions"
TRIGGER_FORMATS = (FORMAT_REACH, FORMAT_SKILL_CREATOR, FORMAT_QUESTIONS)
#: Key carrying a reach row's kind/notes through the in-memory questions rows; never written.
REACH_FIELDS = "_reach"


def detect_trigger_format(data: Any) -> Optional[str]:
    """Which trigger-set format *data* (parsed JSON) is, or ``None``.

    By shape only: a ``{"queries": [...]}`` object is a skill-reach QuerySet, a list
    of ``{query, should_trigger}`` rows is a skill-creator (and agentskills.io
    ``eval_queries.json``) set, and ``{"questions": [...]}`` or a bare list of
    ``question`` rows is a dokimi questions set.
    """
    if isinstance(data, dict):
        if isinstance(data.get("queries"), list):
            return FORMAT_REACH
        if isinstance(data.get("questions"), list):
            return FORMAT_QUESTIONS
        return None
    if isinstance(data, list) and data and all(isinstance(r, dict) for r in data):
        if all("query" in r and "should_trigger" in r for r in data):
            return FORMAT_SKILL_CREATOR
        if all("question" in r or "text" in r for r in data):
            return FORMAT_QUESTIONS
    return None


def _label(value: Any, where: str) -> ExpectedSkill:
    if value is None:
        return None
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list) and all(isinstance(v, str) and v for v in value):
        return list(value) or None
    raise ValueError(f"{where}: expected_skill must be a skill name, a list of names, or null; "
                     f"got {value!r}")


def _text(row: Dict[str, Any], keys: Sequence[str], where: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError(f"{where}: no query text (looked in {', '.join(keys)})")


def from_reach(data: Dict[str, Any]) -> List[TriggerQuery]:
    """A google/skill-reach ``QuerySet``; ``acceptable_skills`` join ``expected_skill``.

    skill-reach credits only ``expected_skill`` and treats ``acceptable_skills`` as
    neutral; dokimi's selection score credits any of them, so a query with
    acceptable siblings scores at least as high here. An id-less query gets
    skill-reach's own default, ``q-001`` by position.
    """
    rows = data.get("queries")
    if not isinstance(rows, list):
        raise ValueError("skill-reach QuerySet has no queries list")
    out = []
    for i, q in enumerate(rows, start=1):
        if not isinstance(q, dict):
            raise ValueError(f"queries[{i - 1}] is not an object")
        qid = str(q.get("id") or q.get("query_id") or f"q-{i:03d}")
        first = _label(q.get("expected_skill"), qid)
        if isinstance(first, list):
            raise ValueError(f"{qid}: skill-reach expected_skill is one name; use acceptable_skills")
        extra = q.get("acceptable_skills") or []
        if not isinstance(extra, list) or not all(isinstance(v, str) and v for v in extra):
            raise ValueError(f"{qid}: acceptable_skills must be a list of skill names")
        if first is None and extra:
            raise ValueError(f"{qid}: acceptable_skills without an expected_skill")
        names = [first, *[v for v in extra if v != first]] if first else []
        label: ExpectedSkill = names if len(names) > 1 else (first or None)
        out.append(TriggerQuery(id=qid, text=_text(q, ("text",), qid), expected_skill=label,
                                reach={k: q[k] for k in ("kind", "notes") if isinstance(q.get(k), str)}))
    return out


def from_skill_creator(rows: List[Dict[str, Any]], target: str) -> List[TriggerQuery]:
    """An anthropics skill-creator / agentskills.io set ``[{query, should_trigger}]`` for *target*.

    The format has no ids, so they are positional (``sc-001``). A negative is
    read as "no skill should load": exact for a catalog of the target alone,
    stricter than the source in a larger one, where loading a rival was not
    something the set's author ruled out.
    """
    if not target:
        raise ValueError("a skill-creator trigger set needs the target skill it was written for")
    out = []
    for i, r in enumerate(rows, start=1):
        qid = f"sc-{i:03d}"
        if not isinstance(r, dict):
            raise ValueError(f"{qid}: row is not an object")
        flag = r.get("should_trigger")
        if not isinstance(flag, bool):
            raise ValueError(f"{qid}: should_trigger must be true or false, got {flag!r}")
        out.append(TriggerQuery(id=qid, text=_text(r, ("query",), qid),
                                expected_skill=target if flag else None))
    return out


def from_questions(data: Any) -> List[TriggerQuery]:
    """A dokimi questions set; a question without ``expected_skill`` stays unlabelled."""
    rows = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise ValueError("questions set has no questions list")
    out = []
    for i, q in enumerate(rows):
        if not isinstance(q, dict):
            raise ValueError(f"questions[{i}] is not an object")
        raw = question_id(q)
        qid = str(f"q{i + 1}" if raw is None else raw)
        text = _text(q, ("question", "text", "prompt", "question_text"), qid)
        tq = TriggerQuery(id=qid, text=text, expected_skill=None, labelled="expected_skill" in q)
        if tq.labelled:
            tq.expected_skill = _label(q["expected_skill"], qid)
        if isinstance(q.get("source_text"), str):
            tq.source_text = q["source_text"]
        if isinstance(q.get("trigger_lint"), dict):
            tq.lint = dict(q["trigger_lint"])
        out.append(tq)
    return out


def read_trigger_set(data: Any, target: Optional[str] = None,
                     fmt: Optional[str] = None) -> tuple:
    """Read a parsed trigger set as ``(format, [TriggerQuery, ...])``.

    *fmt* overrides detection; *target* is required for a skill-creator set,
    which names no skill of its own.
    """
    fmt = fmt or detect_trigger_format(data)
    if fmt == FORMAT_REACH:
        return fmt, from_reach(data)
    if fmt == FORMAT_SKILL_CREATOR:
        return fmt, from_skill_creator(data, target or "")
    if fmt == FORMAT_QUESTIONS:
        return fmt, from_questions(data)
    raise ValueError("not a trigger set this dokimi reads (skill-reach QuerySet, "
                     "skill-creator [{query, should_trigger}], or questions with expected_skill)")
