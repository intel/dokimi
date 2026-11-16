# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""``report eval`` for a deliberate one-condition run (a knowledge run, docs/knowledge-runs.md).

The paired report of :mod:`dokimi.report.eval_report` puts every question of such a run in the
"unpaired" column, with a blank baseline. This page is the one condition's own report: its mean and
spread, by difficulty, the lowest-scoring questions, and every question with its score.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from dokimi.records import BASELINE, Record, is_single_condition, records
from dokimi.report.eval_report import (
    DIFFICULTY_ORDER,
    _answers_by_id,
    _difficulty,
    detect_static_prefix,
    _load_eval,
    _sidecar_question_text,
    _write_qa_json,
    fmt_question,
    generate_report,
)

#: Rows in the lowest-scoring table.
WEAK_SPOTS = 10


def _mean(scores: Sequence[float]) -> Optional[float]:
    return sum(scores) / len(scores) if scores else None


def _cell(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}"


def _condition_row(label: str, qs: Sequence[Record]) -> str:
    """One row of the condition table: questions, scored ones, mean, lowest and highest score."""
    scores = [s for s in (q.score(BASELINE) for q in qs) if s is not None]
    low, high = (min(scores), max(scores)) if scores else (None, None)
    return (f"| {label} | {len(qs)} | {len(scores)} | {_cell(_mean(scores))} | "
            f"{_cell(low)} | {_cell(high)} |")


def _unscored_note(qs: Sequence[Record]) -> List[str]:
    missing = sum(1 for q in qs if q.score(BASELINE) is None)
    if not missing:
        return []
    return [f"_{missing} question(s) have no score: the answer is missing, errored, or was not graded._", ""]


def _difficulty_rows(qs: Sequence[Record]) -> List[str]:
    groups: Dict[Any, List[Record]] = defaultdict(list)
    for q in qs:
        groups[_difficulty(q, "unknown")].append(q)
    order = [d for d in DIFFICULTY_ORDER if d in groups] + [d for d in groups if d not in DIFFICULTY_ORDER]
    return [_condition_row(str(d), groups[d]) for d in order]


def _question_row(q: Record) -> str:
    return (f"| {q.question_id} | {_difficulty(q, '—')} | {_cell(q.score(BASELINE))} | "
            f"{fmt_question(q.question_text)} |")


def render(evals: Sequence[Record], meta: Dict[str, Any], library: str, generated: str) -> List[str]:
    """The markdown lines of the one-condition report for *evals* (the records of a single-condition run)."""
    scored = [q for q in evals if q.score(BASELINE) is not None]
    weakest = sorted(scored, key=lambda q: q.score(BASELINE) or 0.0)[:WEAK_SPOTS]
    lines: List[str] = [
        f"# {library} Knowledge Report",
        f"_Generated: {generated}_",
        "",
        "## Run Configuration",
        "| | |",
        "|---|---|",
        f'| Answer model | {meta.get("answer_model", "—")} ({meta.get("answer_provider", "—")}) |',
        f"| Questions | {len(evals)} ({len(scored)} scored) |",
        "| Conditions | one: `without_docs` (no documentation, skill or persona) |",
        "",
        "## Summary",
        "| Condition | Questions | Scored | Mean | Lowest | Highest |",
        "|---|---:|---:|---:|---:|---:|",
        _condition_row("`without_docs` (knowledge)", evals),
        "",
    ]
    lines += _unscored_note(evals)
    lines += [
        "## By difficulty",
        "| Difficulty | Questions | Scored | Mean | Lowest | Highest |",
        "|---|---:|---:|---:|---:|---:|",
        *_difficulty_rows(evals),
        "",
        f"## Lowest-scoring questions (up to {WEAK_SPOTS})",
        "| QID | Difficulty | Score | Question |",
        "|---|---|---:|---|",
        *[_question_row(q) for q in weakest],
        "",
        "## Questions",
        "| QID | Difficulty | Score | Question |",
        "|---|---|---:|---|",
        *[_question_row(q) for q in evals],
        "",
        "## Notes",
        "",
        "- There is no delta: a knowledge run has one condition, so nothing is paired and no significance "
        "test applies. Read the scores above, or compare with another knowledge run of the same questions "
        "(docs/knowledge-runs.md).",
        "",
    ]
    return lines


def _write_qa(eval_path: str, out_path: str, qa_json_out: Optional[str], evals: Sequence[Record],
              meta: Dict[str, Any]) -> None:
    if qa_json_out is None:
        qa_json_out = os.path.splitext(out_path)[0] + ".qa.json"
    answers_by_id = _answers_by_id(eval_path.replace("/eval/", "/answers/"))

    def _answer_text(a: Optional[Record]) -> Optional[str]:
        answer = a.answer(BASELINE) if a is not None else None
        return answer.text if answer is not None else None

    static_prefix = detect_static_prefix(evals)
    qa_records = [
        {
            "question_id": e.question_id,
            "question_text": _sidecar_question_text(e),
            "difficulty": e.difficulty,
            "source": "static" if static_prefix and e.key.startswith(static_prefix) else "dynamic",
            "with_docs_score": None,
            "without_docs_score": e.score(BASELINE),
            "delta": None,
            "with_docs_answer": None,
            "without_docs_answer": _answer_text(answers_by_id.get(e.key)),
        }
        for e in evals
    ]
    _write_qa_json(qa_json_out, {
        "eval_path": eval_path,
        "report_path": out_path,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_metadata": meta,
        "count": len(qa_records),
        "records": qa_records,
    })
    print(f"✅ Q&A JSON written to: {qa_json_out}")


def write_eval_report(eval_path: str, out_path: str, qa_json_out: Optional[str] = None) -> str:
    """``report eval``: the one-condition page for a knowledge run, else :func:`eval_report.generate_report`."""
    data = _load_eval(eval_path)
    evals = records(data["evaluations"], source=eval_path, kind="evaluations")
    if not is_single_condition(evals):
        return generate_report(eval_path, out_path, qa_json_out)
    meta = data.get("run_metadata", {})
    library = os.path.basename(eval_path).replace(".json", "")
    generated = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = "\n".join(render(evals, meta, library, generated))
    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    _write_qa(eval_path, out_path, qa_json_out, evals, meta)
    scored = [s for s in (q.score(BASELINE) for q in evals) if s is not None]
    print(f"✅ Report written to: {out_path}")
    print(f"   Questions: {len(evals)} ({len(scored)} scored), one condition: without_docs, "
          f"mean {_cell(_mean(scored))}")
    return out_path

