# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Arms.v1 run builders and readers shared by the headroom test modules; no model is called."""

from __future__ import annotations

from dokimi import records


def _run(items, baseline="baseline"):
    """An arms.v1 payload: ``items`` is ``[(question_id, {arm: aggregate-or-None})]``."""
    answers, evaluations = [], []
    for qid, scores in items:
        answers.append({"question_id": qid, "question_text": f"question {qid}",
                        "arms": {arm: {"answer": "x"} for arm in scores}})
        evaluations.append({"question_id": qid,
                            "scores": {arm: ({"aggregate": v} if v is not None else None)
                                       for arm, v in scores.items()}})
    return records.read({"baseline_arm": baseline, "answers": answers, "evaluations": evaluations},
                        source="test")


def _flags(result):
    return {i["question_id"]: i["flag"] for i in result["items"]}
