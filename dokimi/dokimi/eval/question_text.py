# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One rule for a record with no question text, shared by every judge path.

A judge shown an empty question grades the answer against nothing, and the
score still counts. So every path that sends a record to a judge (absolute,
panel, arms, pairwise, checklist, cross-judge, degradation pairs) checks all
of the records it would send to a judge first and refuses the run, naming the
record, before any judge call (and any sampling probe) is paid for. A record
the path would skip anyway (no answer to judge, an answer-error record, no
expected points, no baseline to compare with) is not refused.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable, Optional

from dokimi.utils import question_id

# answers.v1 writes ``question_text``; ``question``/``text`` are the
# question-set spellings, accepted for hand-built inputs.
_KEYS = ("question_text", "question", "text")


class EmptyQuestionError(ValueError):
    """A record handed to a judge has no question text.

    A ``ValueError`` meaning a bad input record, which the CLI reports as
    ``Error: ...`` with exit 2, the code the commands use for bad input (1 is
    the verdict code of ``--strict`` gates and ``eval judge-validity``).
    """


def _label(record: Mapping[str, Any], index: Optional[int]) -> str:
    qid = question_id(dict(record))
    if qid is not None:
        return f"question {qid!r}"
    return f"record #{index}" if index is not None else "a record"


def question_text_of(record: Mapping[str, Any]) -> str:
    """The record's question text by the same key order, or ``""``; never raises.

    For rows about records that are not judged (an answer-error row, say).
    """
    for key in _KEYS:
        text = record.get(key)
        if isinstance(text, str) and text.strip():
            return text
    return ""


def has_answer_to_judge(record: Mapping[str, Any]) -> bool:
    """Whether the record carries an answer a judge would grade (arms or legacy shape)."""
    slots = [record.get("with_docs"), record.get("without_docs")]
    arms = record.get("arms")
    if isinstance(arms, Mapping):
        slots += list(arms.values())
    return any(isinstance(s, Mapping) and s.get("answer") for s in slots)


def require_question_text(record: Mapping[str, Any], index: Optional[int] = None) -> str:
    """The record's question text; :class:`EmptyQuestionError` when it is missing or blank."""
    text = question_text_of(record)
    if text:
        return text
    where = _label(record, index)
    raise EmptyQuestionError(
        f"{where} has no question text (question_text is missing or empty); "
        "a judge cannot grade an answer against an empty question — fix or drop the record"
    )


def require_question_texts(records: Iterable[Mapping[str, Any]]) -> None:
    """Before any judge call, check every record that has an answer to judge.

    Raises on the first one without a question; a record with no answer (an
    answer-error record, say) is never judged and so is not refused.
    """
    for i, record in enumerate(records):
        if has_answer_to_judge(record):
            require_question_text(record, i)
