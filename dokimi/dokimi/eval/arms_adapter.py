# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An ``arms.v1`` run as the ``answers.v1`` and ``eval.v1`` artifacts the pipeline's readers take (#228 step 4).

The pipeline is to hand answering and judging to ``ArmRunner`` (#228 step 4c) while the readers of its
artifacts (the trust gate, ``report generate`` and ``report eval``, the dashboard, RAGAS, grounding, ``baseline``,
``eval score``) stay as they are. :func:`to_binary` is the bridge: a pure function from an arms run to the two
binary artifacts. ``report model-compare`` reads the arms file itself.

* **Arms.** The run's ``baseline_arm`` is ``without_docs``; its one other arm, the context arm, is
  ``with_docs``. A run of the baseline arm alone is a knowledge run (``with_docs: null``, ``run_intent:
  knowledge``). A run with more arms, or with no baseline arm among them, is a :class:`RecordError`.
* **Answers.** A cell keeps its fields; its ``context_chunks`` become ``retrieved_docs``, and ``doc_source`` is
  the first chunk's source, as the answerer writes it. An errored or empty cell is ``null``, and the record's
  ``error`` names it, as a failed condition is in an answers file.
* **Scores.** A score block is copied. ``delta``, ``diagnosis`` and ``retrieval_hit`` are derived as the judge
  derives them (:meth:`dokimi.eval.judge.Judge.evaluate_answers`), from the scores and the answers above;
  ``evaluated_at`` is the run's ``generated_at``, stamped when the judged run was assembled.
* **Not in arms.v1, so absent:** ``retrieval_metadata`` (``fallback_triggered``, see #997), ``token_usage.
  context_chars``, ``token_usage_summary``, ``question_set_hash``, ``judge_placeholder`` (the
  arms judge scores every answer it has). ``method_version`` is carried when the run has one.
* **Incomplete runs.** A run a run-wide error stopped keeps its ``terminated`` block. The partial file of an
  unfinished run (``<out stem>.partial.json``, its ``partial`` block) reads as a ``terminated`` block with its
  counts and no reason, and has no eval: nothing in it was judged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from dokimi.caching import is_empty_reply
from dokimi.eval.diagnoser import diagnose
from dokimi.records import (
    BASELINE, CONTEXT, KNOWLEDGE_INTENT, PAIRED_INTENT, Answer, Record, RecordError, Run, knowledge_only, read,
    record,
)
from dokimi.utils import same_evaluator

#: Top-level fields of an arms run that its eval file carries under the same name.
_JUDGE_FIELDS = ("judge_model", "judge_provider", "judge_temperature", "judge_metrics", "judge_resolved_models")


def to_binary(data: Mapping[str, Any], *, source: str = "<arms>") -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """The ``answers.v1`` and ``eval.v1`` payloads of an ``arms.v1`` run; the eval is ``None`` for an unjudged run.

    The payloads are what ``save_artifact("answers", ...)`` and ``save_artifact("eval", ...)`` take. Raises
    :class:`RecordError` for a file that is not ``arms.v1``, or whose arms are not a baseline and at most one more.
    """
    if not isinstance(data, Mapping) or data.get("schema_version") != "arms.v1":
        raise RecordError(f"{source}: not an arms.v1 artifact")
    # Read past records.read's refusal of a partial file: its stop block says what it is.
    run = read({k: v for k, v in data.items() if k != "partial"}, source=source)
    context, baseline = _binary_arms(run, source)
    intent = KNOWLEDGE_INTENT if context is None else PAIRED_INTENT
    stop = _stop_block(data)
    answers = [_answers_record(r, context, baseline) for r in run]
    answers_out: Dict[str, Any] = {
        **_present(data, "generated_at", "method_version"),
        "run_intent": intent,
        "model": data.get("model"),
        "provider": data.get("provider"),
        "total_questions": len(answers),
        **({"terminated": stop} if stop is not None else {}),
        "answers": answers,
    }
    if "evaluations" not in data:  # an arms run's judging is its evaluations list, all-null scores too
        return answers_out, None
    meta: Dict[str, Any] = {
        "answer_model": data.get("model"),
        "answer_provider": data.get("provider"),
        "judge_model": data.get("judge_model"),
        "judge_provider": data.get("judge_provider"),
        "evaluator_independence_warning": same_evaluator(str(data.get("model")), str(data.get("judge_model"))),
        "run_intent": intent,
        **({"terminated": stop} if stop is not None else {}),
    }
    evaluations = [_eval_record(r, a, context, baseline, meta) for r, a in zip(run, answers, strict=True)]
    return answers_out, {
        # The arms file is assembled after its judging, so its time is the eval's.
        **({"evaluated_at": data["generated_at"]} if "generated_at" in data else {}),
        **_present(data, "method_version", *_JUDGE_FIELDS),
        "total_evaluations": len(evaluations),
        "evaluations": evaluations,
        "run_metadata": meta,
    }


def _binary_arms(run: Run, source: str) -> Tuple[Optional[str], str]:
    """``(context arm or None, baseline arm)``: the two conditions an answers file has room for."""
    baseline = run.baseline
    others = [c for c in run.conditions if c != baseline]
    if baseline is None or baseline not in run.conditions or len(others) > 1:
        raise RecordError(
            f"{source}: arms {list(run.conditions)} with baseline_arm {baseline!r} do not fit answers.v1/eval.v1, "
            "which hold the baseline arm and at most one other")
    return (others[0] if others else None), baseline


def _stop_block(data: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    stopped = data.get("terminated")
    if isinstance(stopped, Mapping):
        return dict(stopped)
    partial = data.get("partial")
    if isinstance(partial, Mapping):
        return {k: partial.get(k) for k in ("questions_requested", "questions_recorded")}
    return None


def _present(data: Mapping[str, Any], *keys: str) -> Dict[str, Any]:
    return {k: data[k] for k in keys if k in data}


def _question_fields(rec: Record) -> Dict[str, Any]:
    return {"question_id": rec.question_id, "question_text": rec.question_text,
            "category": rec.category, "difficulty": rec.difficulty, "persona": rec.persona}


def _answers_record(rec: Record, context: Optional[str], baseline: str) -> Dict[str, Any]:
    out = {**_question_fields(rec), **_present(rec.raw, "library_name")}
    errors: List[str] = []
    for condition, arm in ((CONTEXT, context), (BASELINE, baseline)):
        cell = rec.answer(arm) if arm is not None else None
        if cell is not None and is_empty_reply(cell.text):
            errors.append(f"{arm}: {cell.error or 'empty_reply'}")
            cell = None
        out[condition] = None if cell is None else _answer(cell, with_docs=condition == CONTEXT)
    if errors:
        out["error"] = "; ".join(errors)
    if rec.ground_truth:
        out["ground_truth_chunk"] = rec.ground_truth
    return out


def _answer(cell: Answer, *, with_docs: bool) -> Dict[str, Any]:
    out = {k: v for k, v in cell.raw.items() if k != "context_chunks"}
    if with_docs:
        chunks = cell.raw.get("context_chunks") or []
        out["retrieved_docs"] = list(chunks)
        if chunks:
            out["doc_source"] = chunks[0].get("source", "unknown")
    return out


def _eval_record(rec: Record, answers: Dict[str, Any], context: Optional[str], baseline: str,
                 meta: Mapping[str, Any]) -> Dict[str, Any]:
    binary = record(answers, kind="answers")
    out: Dict[str, Any] = _question_fields(binary)
    errors: List[str] = []
    for condition, arm in ((CONTEXT, context), (BASELINE, baseline)):
        score = rec.scores.get(arm) if arm is not None else None
        judged = binary.answer(condition) is not None
        if score is not None and score.aggregate is None and judged:
            # An answer the arms judge failed on; an unjudged errored answer is the answers file's error.
            errors.append(f"{arm}: {score.error or 'no aggregate'}")
        out[condition] = dict(score.raw) if score is not None and score.aggregate is not None else None
    scored = out[CONTEXT] is not None and out[BASELINE] is not None
    delta = out[CONTEXT]["aggregate"] - out[BASELINE]["aggregate"] if scored else None
    out["delta"] = delta
    if errors:
        out["error"] = "; ".join(errors)
    single = knowledge_only(binary) and meta["run_intent"] == KNOWLEDGE_INTENT
    out["diagnosis"] = diagnose(binary.answer(CONTEXT), delta, context_scored=out[CONTEXT] is not None,
                                single_condition=single)
    out["retrieval_hit"] = binary.retrieval_hit
    return out
