# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""The per-question record of an arms run: one question, every arm's cell, the question's labels."""

from typing import Any, Dict

from dokimi.eval.question_text import ground_truth_from_topics


def question_record(
    library_name: str, q: Dict[str, Any], qid: str, question_text: str, turns: Any,
    arms: Dict[str, Any], *, grounded_from_topics: bool,
) -> Dict[str, Any]:
    rec = {
        "question_id": qid,
        "question_text": question_text,
        "library_name": library_name,
        "category": q.get("category"),
        "difficulty": q.get("difficulty"),
        "persona": q.get("persona"),
        "arms": arms,
    }
    if turns is not None:
        rec["session_turns"] = len(turns)
    # Selection label: present-and-None means "no skill should load", which is
    # why presence is tested rather than truthiness.
    if "expected_skill" in q:
        rec["expected_skill"] = q["expected_skill"]
    ground_truth = q.get("ground_truth_chunk")
    if not ground_truth and grounded_from_topics:
        ground_truth = ground_truth_from_topics(q)
    if ground_truth:
        rec["ground_truth_chunk"] = ground_truth
    return rec
