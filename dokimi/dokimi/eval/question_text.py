# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""One rule for a record with no question text, shared by every judge path.

A judge shown an empty question grades the answer against nothing, and the
score still counts. So every path that sends a record to a judge (absolute,
panel, arms, pairwise, checklist, cross-judge, degradation pairs) checks all
of its records first and refuses the run, naming the record, before any judge
call is paid for.
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

    A ``ValueError``, like ``dokimi.eval.records.RecordError``: both mean a bad
    input record, which the CLI reports as ``Error: ...`` with exit 1.
    """


def _label(record: Any, index: Optional[int]) -> str:
    if isinstance(record, Mapping):
        qid = question_id(dict(record))
    else:  # a parsed record object (``Record``) carries the id as an attribute
        qid = getattr(record, "question_id", None)
    if qid is not None:
        return f"question {qid!r}"
    return f"record #{index}" if index is not None else "a record"


def require_question_text(record: Any, index: Optional[int] = None) -> str:
    """The record's question text; :class:`EmptyQuestionError` when it is missing or blank."""
    if isinstance(record, Mapping):
        texts = [record.get(key) for key in _KEYS]
    else:  # a parsed record object: its reader already picked the key
        texts = [getattr(record, "question_text", None)]
    for text in texts:
        if isinstance(text, str) and text.strip():
            return text
    where = _label(record, index)
    raise EmptyQuestionError(
        f"{where} has no question text (question_text is missing or empty); "
        "a judge cannot grade an answer against an empty question — fix or drop the record"
    )


def require_question_texts(records: Iterable[Any]) -> None:
    """Check every record before any judge call; raise on the first without a question."""
    for i, record in enumerate(records):
        require_question_text(record, i)
