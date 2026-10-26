# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The one reader of answer and eval records.

Every consumer of an ``answers.v1``, ``eval.v1`` or ``arms.v1`` artifact (the judge,
the panel, RAGAS, grounding, the reports, the gates, the judge-reliability checks)
reads question text, answers, context, scores and ground truth through this module,
so a key no writer writes cannot be read by mistake.

Shapes it reads (the writers are ``eval/answerer.py``, ``eval/judge.py``,
``eval/panel.py`` and ``eval/arm_runner.py``):

* **binary**: two conditions under the persisted keys :data:`CONTEXT`
  (``with_docs``, the context arm) and :data:`BASELINE` (``without_docs``). An answers
  record holds ``{answer, retrieved_docs[{source, snippet, relevance_score}], doc_source}``
  per condition; an eval record holds the judge's score block ``{aggregate, <dimensions>}``
  per condition plus the record-level ``retrieval_hit`` the judge and the panel stamp.
  Either payload is ``null`` when that condition failed or was not run.
* **arms**: N named conditions: ``arms{name: {answer, context_chunks[...]}}`` in an
  answers record, ``scores{name: {aggregate, ...} | null}`` in an evaluation. Before the
  ``evaluations`` list existed a score lived inline at ``arms[name].evaluation``
  (``aggregate_score``); that is read too.

Interface
---------
read(data) / load(path) -> Run
    A whole artifact: ``{"answers": [...]}``, ``{"evaluations": [...]}``, both (an
    ``arms.v1`` file, joined by question id) or a bare record list. With
    ``section="answers"`` (or ``"evaluations"``) only that list is read, and an
    artifact without it is an error: a command that needs answers refuses an eval file.
records(items) / record(item)
    Records from persisted dicts. A :class:`Record` passes through unchanged, so a
    function documented as taking records accepts either.
join(answers, evaluations)
    Attach scores to the answers they score, by question id.
arm_answers(record) / arm_scores(record) / arm_names(record) / has_arms(records)
    Only what an arms run holds under ``arms`` and ``scores``, for the readers
    that judge arms and refuse a binary file. ``arm_names`` also lists the arms
    that ran and produced nothing (``null``).

Values are read, not normalised: a question id stays the ``str`` or ``int`` it was
written as and an aggregate stays the ``int`` or ``float`` it was written as, so a
report that prints them prints what the file says.

Errors: :class:`RecordError` (a ``ValueError``) for a missing or unparseable file, an
artifact with no record list, a record that is not an object, has no
``question_id`` or has one that is neither a string nor an integer, a condition payload that is neither an object nor ``null``, an
``arms``/``scores`` field that is not an object, an ``expected_skill`` that is not a
name, a list of names or ``null``, and duplicate question ids where records are
joined. A missing or non-numeric aggregate is not an error: it is an unscored
condition, and :meth:`Record.score` returns ``None``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

#: The binary context arm, under its persisted key.
CONTEXT = "with_docs"
#: The binary baseline, under its persisted key.
BASELINE = "without_docs"
BINARY = (CONTEXT, BASELINE)

#: ``doc_source`` values the answerer writes when no document reached the prompt.
NO_RETRIEVAL_SOURCES = frozenset({"fallback_none", "none"})

#: A question id as persisted: ``questions.v1`` allows strings and integers.
QuestionId = Union[str, int]


class RecordError(ValueError):
    """An artifact or record that is not one of the shapes this module reads."""


@dataclass(frozen=True)
class Doc:
    """One context document an answer was given: a retrieved doc or an injected chunk."""

    text: str
    source: Optional[str] = None
    relevance: Optional[float] = None


@dataclass(frozen=True)
class Answer:
    """One condition's answer to one question."""

    #: The answer text as written (``""`` stays ``""``); ``None`` when there is none.
    text: Optional[str]
    docs: tuple[Doc, ...] = ()
    doc_source: Optional[str] = None
    error: Optional[str] = None
    #: The persisted payload, for fields this module does not model (metrics,
    #: transcripts, metadata). Read-only by convention.
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def context(self) -> list[str]:
        """The text of every context document, in order (``""`` for one with no text)."""
        return [d.text for d in self.docs]


@dataclass(frozen=True)
class Score:
    """One condition's judged score for one question."""

    #: The 0-100 aggregate as written (``int`` or ``float``); ``None`` when the judge
    #: produced none or wrote something that is not a number.
    aggregate: Optional[float]
    error: Optional[str] = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def agreement(self) -> Optional[float]:
        """Panel inter-rater agreement (0-1), when a panel scored it."""
        return _number(self.raw.get("agreement_score"))


@dataclass(frozen=True)
class Record:
    """One question: its text and metadata, and per condition its answer and score."""

    question_id: QuestionId
    question_text: str = ""
    category: Any = None
    difficulty: Any = None
    persona: Any = None
    ground_truth: Optional[str] = None
    answers: Mapping[str, Answer] = field(default_factory=dict)
    scores: Mapping[str, Score] = field(default_factory=dict)
    #: Every condition the record lists, in persisted order, ``null`` ones included.
    conditions: tuple[str, ...] = ()
    #: Record-level failure (the whole question errored before any condition ran).
    error: Optional[str] = None
    diagnosis: Optional[Mapping[str, Any]] = None
    #: Whether retrieval put at least one document into the binary context arm's
    #: prompt. An evaluation carries it as the ``retrieval_hit`` the judge and the
    #: panel stamp; an answers record derives it from its context answer's
    #: ``retrieved_docs`` and ``doc_source``. ``None`` when unrecorded: an eval file
    #: judged before the field existed, or no context-arm answer.
    retrieval_hit: Optional[bool] = None
    #: The skill-selection label ``ArmRunner`` copies from the question's
    #: ``expected_skill``: the acceptable skill names, ``()`` when no skill should
    #: load, ``None`` when the record carries no label (not scored for selection).
    expected_skills: Optional[tuple[str, ...]] = None
    #: The persisted record, for fields this module does not model. Read-only by
    #: convention. A joined record's is the answers record over the evaluation.
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """The question id as a string, which is how records are joined."""
        return str(self.question_id)

    @property
    def nulls(self) -> tuple[str, ...]:
        """Conditions listed with a ``null`` payload: run, but produced nothing."""
        return tuple(c for c in self.conditions if c not in self.answers and c not in self.scores)

    @property
    def delta(self) -> Optional[float]:
        """The persisted record-level ``delta`` (binary eval records), when it is a number."""
        return _number(self.raw.get("delta"))

    def answer(self, condition: str) -> Optional[Answer]:
        return self.answers.get(condition)

    def score(self, condition: str) -> Optional[float]:
        """The aggregate for ``condition``; ``None`` when unscored or absent."""
        s = self.scores.get(condition)
        return s.aggregate if s is not None else None


@dataclass(frozen=True)
class Run:
    """A whole artifact's records plus what it says about its conditions."""

    records: tuple[Record, ...]
    #: ``"binary"`` or ``"arms"``.
    shape: str
    #: Declared (``arms.v1`` ``arms``) or observed condition names, in order.
    conditions: tuple[str, ...]
    #: The control condition: ``without_docs`` for binary runs, ``baseline_arm`` for
    #: arms runs (``None`` when an arms run does not declare one).
    baseline: Optional[str]
    #: Top-level fields other than the record lists (model, provider, judge_model,
    #: library_name, baseline_arm, the declared ``arms`` list, ...).
    meta: Mapping[str, Any] = field(default_factory=dict)

    @property
    def treatments(self) -> tuple[str, ...]:
        return tuple(c for c in self.conditions if c != self.baseline)

    @property
    def judged(self) -> bool:
        return any(r.scores for r in self.records)

    def __len__(self) -> int:
        return len(self.records)

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records)


RecordLike = Union[Record, Mapping[str, Any]]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def load(path: Union[str, Path], *, section: Optional[str] = None) -> Run:
    """Read an artifact file; every failure is a :class:`RecordError` naming ``path``."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RecordError(f"file not found: {path}") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise RecordError(f"cannot read {path}: {exc}") from None
    except json.JSONDecodeError as exc:
        raise RecordError(f"invalid JSON in {path}: {exc}") from None
    return read(data, source=str(path), section=section)


def read(data: Any, *, source: str = "<input>", section: Optional[str] = None) -> Run:
    """Records of an artifact: a wrapped ``answers``/``evaluations`` object or a bare list."""
    if section not in (None, "answers", "evaluations"):
        raise ValueError(f"section must be 'answers' or 'evaluations', not {section!r}")
    if isinstance(data, Run):
        return data
    if isinstance(data, list):
        return _run(records(data, source=source, kind=section), {})
    if not isinstance(data, Mapping):
        raise RecordError(f"{source}: expected a JSON object or a list of records, got {type(data).__name__}")
    answers = _list(data, "answers", source) if section in (None, "answers") else None
    evaluations = _list(data, "evaluations", source) if section in (None, "evaluations") else None
    if section is not None and (answers if section == "answers" else evaluations) is None:
        raise RecordError(f"{source}: no '{section}' list")
    if answers is None and evaluations is None:
        raise RecordError(f"{source}: no 'answers' or 'evaluations' list")
    if answers is not None and evaluations is not None:
        recs = join(answers, evaluations, source=source)
    elif answers is not None:
        recs = records(answers, source=f"{source} answers", kind="answers")
    else:
        recs = records(evaluations or [], source=f"{source} evaluations", kind="evaluations")
    meta = {k: v for k, v in data.items() if k not in ("answers", "evaluations")}
    return _run(recs, meta)


def records(items: Iterable[RecordLike], *, source: str = "<input>", kind: Optional[str] = None) -> list[Record]:
    """Parse persisted record dicts; :class:`Record` instances pass through.

    ``kind`` (``"answers"`` or ``"evaluations"``) says what a binary record's
    condition payloads are; without it a record is an evaluation when it carries a
    ``delta`` or a score block.
    """
    if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, Iterable):
        raise RecordError(f"{source}: expected a list of records, got {type(items).__name__}")
    return [record(item, source=source, index=i, kind=kind) for i, item in enumerate(items)]


def record(item: RecordLike, *, source: str = "<input>", index: int = 0, kind: Optional[str] = None) -> Record:
    """Parse one persisted record (an answers record, an evaluation, or both joined)."""
    if isinstance(item, Record):
        return item
    where = f"{source}[{index}]"
    if not isinstance(item, Mapping):
        raise RecordError(f"{where}: expected an object, got {type(item).__name__}")
    qid = _question_id(item, where)
    if qid is None:
        raise RecordError(f"{where}: missing question_id")

    answers: dict[str, Answer] = {}
    scores: dict[str, Score] = {}
    conditions: list[str] = []
    # A stray `scores: null` on a binary record does not make it an arms record.
    if item.get("arms") is not None or item.get("scores") is not None:
        for name, payload in _mapping(item, "arms", where).items():
            _check_payload(payload, f"{where}.arms.{name}")
            conditions.append(name)
            if isinstance(payload, Mapping):
                answers[name] = _answer(payload)
                legacy = payload.get("evaluation")
                if isinstance(legacy, Mapping):
                    scores[name] = _score(legacy, key="aggregate_score")
        for name, payload in _mapping(item, "scores", where).items():
            _check_payload(payload, f"{where}.scores.{name}")
            conditions.append(name)
            if isinstance(payload, Mapping):
                scores[name] = _score(payload)
            else:
                # An explicit null scores the arm as failed: a legacy inline score under
                # the same name does not survive it.
                scores.pop(name, None)
    else:
        is_eval = kind == "evaluations" or (kind is None and _looks_judged(item))
        for name in BINARY:
            if name not in item:
                continue
            payload = item[name]
            _check_payload(payload, f"{where}.{name}")
            conditions.append(name)
            if isinstance(payload, Mapping):
                if is_eval:
                    scores[name] = _score(payload)
                else:
                    answers[name] = _answer(payload)

    diagnosis = item.get("diagnosis")
    hit = item.get("retrieval_hit")
    return Record(
        question_id=qid,
        question_text=_question_text(item),
        category=item.get("category"),
        difficulty=item.get("difficulty"),
        persona=item.get("persona"),
        ground_truth=_text(item.get("ground_truth_chunk")),
        answers=answers,
        scores=scores,
        conditions=tuple(dict.fromkeys(conditions)),
        error=_text(item.get("error")),
        diagnosis=diagnosis if isinstance(diagnosis, Mapping) else None,
        retrieval_hit=hit if isinstance(hit, bool) else _retrieval_hit(answers.get(CONTEXT), conditions),
        expected_skills=_expected_skills(item, where),
        raw=item,
    )


def join(
    answers: Iterable[RecordLike], evaluations: Iterable[RecordLike], *, source: str = "<input>"
) -> list[Record]:
    """Each answers record with the scores of the evaluation for the same question.

    Order follows ``answers``; evaluations with no answers record are appended in
    their own order. Question metadata comes from the answers record, falling back
    to the evaluation's where the answers record has none; a stamped
    ``retrieval_hit`` on the evaluation wins over the one an answer implies.
    """
    ans = records(answers, source=f"{source} answers", kind="answers")
    evs = records(evaluations, source=f"{source} evaluations", kind="evaluations")
    by_key: dict[str, Record] = {}
    for r in evs:
        if r.key in by_key:
            raise RecordError(f"{source}: duplicate question_id {r.question_id!r} in evaluations")
        by_key[r.key] = r
    out: list[Record] = []
    seen: set[str] = set()
    for a in ans:
        if a.key in seen:
            raise RecordError(f"{source}: duplicate question_id {a.question_id!r} in answers")
        seen.add(a.key)
        e = by_key.get(a.key)
        if e is None:
            out.append(a)
            continue
        out.append(Record(
            question_id=a.question_id,
            question_text=a.question_text or e.question_text,
            category=a.category if a.category is not None else e.category,
            difficulty=a.difficulty if a.difficulty is not None else e.difficulty,
            persona=a.persona if a.persona is not None else e.persona,
            ground_truth=a.ground_truth or e.ground_truth,
            answers=a.answers,
            # A condition the evaluation lists, null or not, takes its score from the
            # evaluation: an answer's legacy inline score does not outlive an explicit null.
            scores={**{c: s for c, s in a.scores.items() if c not in e.conditions}, **e.scores},
            conditions=tuple(dict.fromkeys([*a.conditions, *e.conditions])),
            error=a.error or e.error,
            diagnosis=e.diagnosis or a.diagnosis,
            retrieval_hit=e.retrieval_hit if e.retrieval_hit is not None else a.retrieval_hit,
            expected_skills=a.expected_skills if a.expected_skills is not None else e.expected_skills,
            raw={**e.raw, **a.raw},
        ))
    out.extend(e for e in evs if e.key not in seen)
    return out


def arm_answers(rec: Record) -> Mapping[str, Answer]:
    """The answers a record holds under ``arms``; empty for a binary record.

    The arm judges (pairwise, checklist, cross-judge) read only these: a binary
    record's ``with_docs``/``without_docs`` answers are not arms to them.
    """
    return rec.answers if isinstance(rec.raw.get("arms"), Mapping) else {}


def arm_scores(rec: Record) -> Mapping[str, Score]:
    """The scores a record holds under ``scores``, the ``evaluations`` field of an arms run.

    Not a binary record's condition scores, and not a legacy inline
    ``arms[name].evaluation``: an arms run's stored judge is its ``evaluations``.
    """
    block = rec.raw.get("scores")
    if not isinstance(block, Mapping):
        return {}
    return {name: rec.scores[name] for name, payload in block.items()
            if isinstance(payload, Mapping) and name in rec.scores}


def arm_names(rec: Record) -> tuple[str, ...]:
    """Every arm an arms record lists, ``null`` ones included; ``()`` for a binary record.

    A ``null`` arm is one that ran and produced nothing. The judges and the consistency
    checks need it: dropping it makes a run look as if it never had that arm.
    """
    if rec.raw.get("arms") is None and rec.raw.get("scores") is None:
        return ()
    return tuple(c for c in rec.conditions if c not in BINARY)


def has_arms(recs: Iterable[Record]) -> bool:
    """Whether any record holds answers under ``arms`` (an arms run, not a binary file)."""
    return any(isinstance(r.raw.get("arms"), Mapping) for r in recs)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _run(recs: Sequence[Record], meta: dict[str, Any]) -> Run:
    declared = meta.get("arms")
    names = [a for a in declared if isinstance(a, str)] if isinstance(declared, list) else []
    baseline = meta.get("baseline_arm")
    arms_shape = bool(names) or "baseline_arm" in meta or any(
        set(r.conditions) - set(BINARY) for r in recs
    )
    if arms_shape:
        observed = [c for r in recs for c in r.conditions]
        conditions = tuple(dict.fromkeys([*names, *observed]))
        return Run(tuple(recs), "arms", conditions, baseline if isinstance(baseline, str) and baseline else None,
                   meta)
    return Run(tuple(recs), "binary", BINARY, BASELINE, meta)


def _question_id(item: Mapping[str, Any], where: str) -> Optional[QuestionId]:
    """``question_id``, else ``id``; ``0`` is an id, ``None``/``""`` are not.

    ``question_id`` wins over ``id``: it is the field every answers/eval writer uses.
    A present id that is neither a string nor an integer is a :class:`RecordError`.
    """
    for key in ("question_id", "id"):
        value = item.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return value
        raise RecordError(f"{where}: {key} must be a string or an integer, got {value!r}")
    return None


def _list(data: Mapping[str, Any], key: str, source: str) -> Optional[list[Any]]:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list):
        raise RecordError(f"{source}: '{key}' must be a list, got {type(value).__name__}")
    return value


def _mapping(item: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    value = item.get(key)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RecordError(f"{where}: '{key}' must be an object, got {type(value).__name__}")
    return value


def _check_payload(payload: Any, where: str) -> None:
    if payload is not None and not isinstance(payload, Mapping):
        raise RecordError(f"{where}: expected an object or null, got {type(payload).__name__}")


def _expected_skills(item: Mapping[str, Any], where: str) -> Optional[tuple[str, ...]]:
    # Presence, not truthiness: ``expected_skill: null`` means "no skill should load".
    if "expected_skill" not in item:
        return None
    label = item["expected_skill"]
    names = label if isinstance(label, list) else [label] if label is not None else []
    if not all(isinstance(n, str) for n in names):
        raise RecordError(f"{where}: expected_skill must be a skill name, a list of names, "
                          f"or null; got {label!r}")
    return tuple(n for n in names if n)


def _looks_judged(item: Mapping[str, Any]) -> bool:
    """A bare binary record is an evaluation when it carries a delta or a score block."""
    if "delta" in item:
        return True
    return any(isinstance(item.get(c), Mapping) and "aggregate" in item[c] for c in BINARY)


def _retrieval_hit(context: Optional[Answer], conditions: Sequence[str]) -> Optional[bool]:
    """What a binary answers record says about retrieval for its context arm.

    ``False`` when the answerer stamped a no-retrieval ``doc_source`` or passed no
    ``retrieved_docs``; ``None`` when there is no binary context-arm answer.
    """
    if context is None or set(conditions) - set(BINARY):
        return None
    if context.doc_source in NO_RETRIEVAL_SOURCES:
        return False
    return bool(context.raw.get("retrieved_docs"))


def _question_text(item: Mapping[str, Any]) -> str:
    # question_text is what answers and eval records carry; the panel's eval
    # output writes `question`, a question record `text`.
    for key in ("question_text", "question", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _text(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> Optional[float]:
    """``value`` when it is a finite int or float (not a bool), unchanged; else ``None``.

    ``json.loads`` reads ``NaN`` and ``Infinity``; neither is a score a mean can use.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return None
    return value if finite else None


def _answer(payload: Mapping[str, Any]) -> Answer:
    docs_raw = payload.get("retrieved_docs")
    if docs_raw is None:
        docs_raw = payload.get("context_chunks")
    docs = tuple(
        Doc(
            text=str(d.get("snippet") or d.get("content") or ""),
            source=_text(d.get("source")),
            relevance=_number(d.get("relevance_score")),
        )
        for d in (docs_raw if isinstance(docs_raw, list) else [])
        if isinstance(d, Mapping) and d
    )
    text = payload.get("answer")
    doc_source = payload.get("doc_source")
    return Answer(
        text=text if isinstance(text, str) else None,
        docs=docs,
        doc_source=doc_source if isinstance(doc_source, str) else None,
        error=_text(payload.get("error")),
        raw=payload,
    )


def _score(payload: Mapping[str, Any], *, key: str = "aggregate") -> Score:
    # A score block keeps its aggregate under ``aggregate``; the legacy inline
    # ``arms[name].evaluation`` kept it under ``aggregate_score``.
    return Score(aggregate=_number(payload.get(key)), error=_text(payload.get("error")), raw=payload)
