# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``eval reference``: grade an answers or arms file with its questions' ``grading`` checks.

The answers come from the usual answer track (``answers generate``, ``arms run``) or from a
native tool's log (:mod:`dokimi.task_formats.imports.native_logs`); the checks are
:mod:`dokimi.eval.reference_checks`. The result is the artifact the judge would have written,
so reports, gates and model comparisons read it unchanged:

- a binary answers file (``with_docs``/``without_docs``) gives an ``eval.v1`` artifact whose
  condition blocks hold ``aggregate`` (0-100, the weighted share of checks passed), ``passed``
  and the per-check results;
- an ``arms.v1`` file gives the same file back with ``evaluations`` (``scores`` per arm).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from dokimi.eval.reference_checks import Ask, Grade, grade, validate_grading
from dokimi.records import NO_RETRIEVAL_SOURCES, Record, Run, read

SCORER = "reference"


@dataclass
class ConditionTally:
    n: int = 0
    passed: int = 0
    unscored: int = 0
    judge_unscored: int = 0
    score_sum: float = 0.0

    def add(self, g: Grade) -> None:
        score = g.score
        if score is None:
            self.unscored += 1
            return
        self.n += 1
        self.score_sum += score
        self.passed += bool(g.passed)
        # Counted on the checks that ran: a pass here may not include the judged checks.
        self.judge_unscored += bool(g.unjudged)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "unscored": self.unscored,
            "judge_unscored": self.judge_unscored,
            "pass_rate": round(self.passed / self.n, 4) if self.n else None,
            "mean_score": round(self.score_sum / self.n, 4) if self.n else None,
        }


@dataclass
class ReferenceRun:
    run: Run
    evaluations: list[dict[str, Any]]
    tally: dict[str, ConditionTally] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


def grading_by_id(questions: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """Each question's ``grading`` block by id; refuses a set where a block cannot be graded."""
    out: dict[str, Mapping[str, Any]] = {}
    problems: list[str] = []
    for q in questions:
        if "grading" not in q:
            continue
        qid = str(q.get("id"))
        found = validate_grading(q["grading"])
        problems.extend(f"{qid}: {p}" for p in found)
        out[qid] = q["grading"]
    if problems:
        raise ValueError("questions with a grading block that cannot be graded: " + "; ".join(problems[:10]))
    return out


def _grade_record(rec: Record, grading: Mapping[str, Any], ask: Optional[Ask]) -> dict[str, Grade]:
    out: dict[str, Grade] = {}
    for name, answer in rec.answers.items():
        # A no-retrieval placeholder ("[FALLBACK: ...]") is not the model's answer: unscored.
        text = None if answer.error or answer.doc_source in NO_RETRIEVAL_SOURCES else answer.text
        out[name] = grade(grading, rec.question_text, text, ask)
    return out


def score(data: Any, questions: list[Mapping[str, Any]], *, ask: Optional[Ask] = None,
          source: str = "<answers>", concurrency: int = 4) -> ReferenceRun:
    """Grade every answer in ``data`` (an answers or arms payload)."""
    run = read(data, source=source, section="answers" if isinstance(data, Mapping) and "answers" in data else None)
    gradings = grading_by_id(questions)
    graded = [r for r in run if r.key in gradings]
    missing = [r.key for r in run if r.key not in gradings]

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        grades = list(pool.map(lambda r: _grade_record(r, gradings[r.key], ask), graded))

    out = ReferenceRun(run=run, evaluations=[], missing=missing)
    for rec, per_condition in zip(graded, grades, strict=True):
        blocks = {}
        for name, g in per_condition.items():
            out.tally.setdefault(name, ConditionTally()).add(g)
            blocks[name] = {**g.as_dict(), "scorer": SCORER}
        out.evaluations.append(_evaluation(run, rec, blocks))
    return out


def _evaluation(run: Run, rec: Record, blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ev: dict[str, Any] = {"question_id": rec.question_id, "question_text": rec.question_text}
    if run.shape == "arms":
        ev["scores"] = blocks
        return ev
    for name in ("with_docs", "without_docs"):
        ev[name] = blocks.get(name)
    with_docs, without = (blocks.get(n, {}).get("aggregate") for n in ("with_docs", "without_docs"))
    ev["delta"] = round(with_docs - without, 1) if with_docs is not None and without is not None else None
    return ev


def artifact(result: ReferenceRun, data: Any, *, judge_model: Optional[str], judge_provider: Optional[str],
             questions_path: str) -> tuple[str, dict[str, Any]]:
    """``(kind, payload)`` to save: ``eval`` for a binary run, ``arms`` for an arms run."""
    meta = {
        "scorer": SCORER,
        "questions": questions_path,
        "summary": {name: t.as_dict() for name, t in result.tally.items()},
        "ungraded_questions": len(result.missing),
    }
    judge = {"judge_model": judge_model, "judge_provider": judge_provider}
    if result.run.shape == "arms":
        # arms.v1 requires judge strings next to `evaluations`; with no judge, the scorer is the stamp.
        judge = {k: v or SCORER for k, v in judge.items()}
        base = dict(data) if isinstance(data, Mapping) else {"answers": data}
        return "arms", {**base, **judge, "evaluations": result.evaluations, "reference_scoring": meta}
    return "eval", {
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **judge,
        "total_evaluations": len(result.evaluations),
        "evaluations": result.evaluations,
        "run_metadata": meta,
    }
