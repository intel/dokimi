# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""An ``arms.v1`` run as the ``answers.v1`` and ``eval.v1`` artifacts the pipeline's readers take (#228 step 4).

The pipeline is to hand answering and judging to ``ArmRunner`` (#228 step 4c) while the readers of its
artifacts (the trust gate, ``report generate`` and ``report eval``, the dashboard, RAGAS, grounding, ``baseline``,
``eval score``) stay as they are. :func:`to_binary` is the bridge: a pure function from an arms run to the two
binary artifacts. ``report model-compare`` reads the arms file itself.

* **Arms.** The run's ``baseline_arm`` is ``without_docs``; it must be a baseline arm. Its one other arm, the
  context arm, is ``with_docs``; it must be a docs arm. Each answered cell records its arm's kind (``arm_kind``
  ``baseline``; ``docs`` or ``mcp_doc``); only an unanswered cell, a stop's, may record none. In a stopped run (a
  ``terminated`` or ``partial`` block), an arm the stop reached before it answered records no kind: it is taken as the
  docs arm, with ``with_docs: null``. A non-docs arm stopped that early cannot be told apart, and converts too. A run
  of the baseline arm alone is a knowledge run (``with_docs: null``, ``run_intent: knowledge``). A run with more arms,
  with no baseline arm among them, with a baseline arm of another kind, or whose other arm is not a docs arm, is a
  :class:`RecordError`, and so is a session run (``session_turns``), whose answers are the last turn's.
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
    BASELINE, CONTEXT, KNOWLEDGE_INTENT, PAIRED_INTENT, Answer, Record, RecordError, Run, arm_scores, knowledge_only,
    read, record,
)
from dokimi.utils import same_evaluator

#: The ``arm_kind`` of a ``DocTreatment`` arm (``treatments/factory.py``): retrieval into the prompt.
_DOC_KINDS = frozenset({"docs", "mcp_doc"})
#: Top-level fields of an arms run that its eval file carries under the same name.
_JUDGE_FIELDS = ("judge_model", "judge_provider", "judge_temperature", "judge_metrics", "judge_resolved_models")


def to_binary(data: Mapping[str, Any], *, source: str = "<arms>") -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """The ``answers.v1`` and ``eval.v1`` payloads of an ``arms.v1`` run; the eval is ``None`` for an unjudged run.

    The payloads are what ``save_artifact("answers", ...)`` and ``save_artifact("eval", ...)`` take. Raises
    :class:`RecordError` for a file that is not ``arms.v1``, a session run, or a run whose arms are not a baseline and
    at most one docs arm.
    """
    if not isinstance(data, Mapping) or data.get("schema_version") != "arms.v1":
        raise RecordError(f"{source}: not an arms.v1 artifact")
    # Read past records.read's refusal of a partial file: its stop block says what it is.
    run = read({k: v for k, v in data.items() if k != "partial"}, source=source)
    if data.get("session") is not None or any(r.raw.get("session_turns") for r in run):
        # A session cell's answer is its last turn's; a binary record would pair it with the first question.
        raise RecordError(f"{source}: a session run (session_turns) has no answers.v1/eval.v1 reading: "
                          "each answer is the last turn's, and these files pair it with the first question")
    stop = _stop_block(data)
    context, baseline = _binary_arms(run, source, stopped=stop is not None)
    intent = KNOWLEDGE_INTENT if context is None else PAIRED_INTENT
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


def _binary_arms(run: Run, source: str, *, stopped: bool) -> Tuple[Optional[str], str]:
    """``(context arm or None, baseline arm)``: the two conditions an answers file has room for."""
    baseline = run.baseline
    others = [c for c in run.conditions if c != baseline]
    if baseline is None or baseline not in run.conditions or len(others) > 1:
        raise RecordError(
            f"{source}: arms {list(run.conditions)} with baseline_arm {baseline!r} do not fit answers.v1/eval.v1, "
            "which hold the baseline arm and at most one other")
    base = _cell_kinds(run, baseline)
    if _named(base) - {"baseline"} or _unnamed_answer(base):
        # A --baseline-arm docs run would read as docs against no docs (BaselineTreatment records "baseline").
        raise RecordError(f"{source}: baseline_arm {baseline!r} (arm_kind {_shown(base)}) is not a baseline arm, "
                          "the one arm without_docs holds")
    if not others:
        return None, baseline
    cells = _cell_kinds(run, others[0])
    # Every answer must name a docs arm. A run stopped before the arm answered has only stop cells, which
    # record no kind (arm_stop.py): with_docs gets null from them, which no reader can misread.
    if _named(cells) - _DOC_KINDS or _unnamed_answer(cells) or (not _named(cells) and not stopped):
        # with_docs is what retrieval put in the prompt: another arm would read as a retrieval miss.
        raise RecordError(
            f"{source}: arm {others[0]!r} (arm_kind {_shown(cells)}) is not a docs arm "
            f"({', '.join(sorted(_DOC_KINDS))}), the one arm with_docs holds")
    return others[0], baseline


def _cell_kinds(run: Run, arm: str) -> List[Tuple[bool, Optional[str]]]:
    """``(answered, arm_kind or None)`` for each cell *arm* holds; a stop cell records no kind."""
    out = []
    for r in run:
        if (cell := r.answer(arm)) is not None:
            kind = (cell.raw.get("metadata") or {}).get("arm_kind")
            out.append((not is_empty_reply(cell.text), kind if isinstance(kind, str) and kind else None))
    return out


def _named(cells: List[Tuple[bool, Optional[str]]]) -> set[str]:
    return {kind for _, kind in cells if kind is not None}


def _unnamed_answer(cells: List[Tuple[bool, Optional[str]]]) -> bool:
    """An answer whose cell names no arm: it cannot be told to be the arm its slot holds."""
    return any(answered and kind is None for answered, kind in cells)


def _shown(cells: List[Tuple[bool, Optional[str]]]) -> str:
    named = sorted(_named(cells))
    return f"{named}, and an answer with none" if named and _unnamed_answer(cells) else str(named or "unrecorded")


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
        # The chunks records.Answer reads as docs: the non-empty objects of a list.
        raw = cell.raw.get("context_chunks")
        chunks = [c for c in raw if isinstance(c, Mapping) and c] if isinstance(raw, list) else []
        out["retrieved_docs"] = chunks
        if chunks:
            out["doc_source"] = chunks[0].get("source", "unknown")
    return out


def _eval_record(rec: Record, answers: Dict[str, Any], context: Optional[str], baseline: str,
                 meta: Mapping[str, Any]) -> Dict[str, Any]:
    binary = record(answers, kind="answers")
    out: Dict[str, Any] = _question_fields(binary)
    errors: List[str] = []
    for condition, arm in ((CONTEXT, context), (BASELINE, baseline)):
        # The run's evaluations list only, not a legacy inline arms[name].evaluation (records.arm_scores).
        score = arm_scores(rec).get(arm) if arm is not None else None
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
