# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Run an N-arm treatment comparison: generate (and optionally judge) answers.

Generalizes the binary ``with_docs``/``without_docs`` answerer to an arbitrary
set of :class:`~dokimi.treatments.base.Treatment` arms — documentation
injection, MCP-served docs, agent persona prompts, and skills — scored by the
same LLM-as-judge used elsewhere.
"""

import logging
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from dokimi.utils import question_id  # noqa: F401  (re-exported: callers import it from here)
from dokimi.artifacts import save_artifact
from dokimi.llm import llm_call_with_usage
from dokimi.defaults import DEFAULT_ANSWER_MODEL, DEFAULT_ANSWER_PROVIDER
from dokimi.eval import stats as arm_stats
from dokimi.eval.interactions import interaction_significance
from dokimi.eval.arm_summary import (
    cost_summary, summarize_agentic_usage, summarize_skill_selection,
)
from dokimi.eval.cells import Cell
from dokimi.eval.question_text import question_text_of, require_question_texts
from dokimi.records import Record, RecordLike, arm_names, records as as_records
from dokimi.eval.judge_context import PROTOCOL as JUDGE_CONTEXT_PROTOCOL
from dokimi.eval.judge_context import SOURCE_STORED
from dokimi.eval.judge_context import stamp_for as judge_context_stamp
from dokimi.eval.judge_context import JudgeContextStore
from dokimi.eval.judge_context import build as build_judge_context
from dokimi.eval.judge_context import cite as cite_judge_materials
from dokimi.eval.judge_context import join_injected
from dokimi.eval.session import REMINDER_ARM_SUFFIX, run_session, session_turns_of
from dokimi.metrics.usage import to_record
from dokimi.treatments.base import Treatment

logger = logging.getLogger(__name__)

# Split so the stable half can be a cacheable prompt prefix: providers only
# reuse a *prefix*, so the context has to precede the question. Note this
# reordered the prompt relative to earlier runs (the question used to come
# first), which changes model output — scores from before and after are not
# directly comparable.
CONTEXT_PROMPT_PREFIX = """You are answering a user question.

**Relevant context:**
{context}

"""

CONTEXT_PROMPT_SUFFIX = """**Question:** {question}

**Task:**
Answer the question using the provided context above where relevant. Be specific, accurate, and actionable. Include code examples if relevant.

**Answer:**"""

#: The full prompt. Concatenation, not a third copy of the text, so the cached
#: two-block form and the plain single-string form stay byte-identical.
ANSWER_PROMPT_WITH_CONTEXT = CONTEXT_PROMPT_PREFIX + CONTEXT_PROMPT_SUFFIX

ANSWER_PROMPT_PLAIN = """Answer the following user question. Be specific and include code examples if relevant.

**Question:** {question}

**Answer:**"""

MAX_CONTEXT_CHARS = 15_000

# Curated golden sets carry verified answer statements in ``expected_topics``,
# which are usable as judge ground truth. Auto-generated sets put short keyword
# labels in the same field (measured: ~14-19 chars/topic, versus 78-207 for the
# golden sets), and grading a full answer against bare keywords would penalise
# correct answers. Only promote topics that are prose-length.
MIN_GROUND_TRUTH_TOPIC_CHARS = 40


def _ground_truth_from_topics(q: Dict[str, Any]) -> Optional[str]:
    """Build judge ground truth from ``expected_topics``, when they are substantive.

    Returns ``None`` for keyword-style topics so that such questions keep using
    the ungrounded rubric rather than being scored against a word list.
    """
    topics = q.get("expected_topics") or []
    if not isinstance(topics, list):
        return None
    statements = [str(t).strip() for t in topics if str(t).strip()]
    if not statements:
        return None
    mean_len = sum(len(s) for s in statements) / len(statements)
    if mean_len < MIN_GROUND_TRUTH_TOPIC_CHARS:
        return None
    return "\n".join(f"- {s}" for s in statements)


def _static_arm_kind(treatment: Treatment) -> Dict[str, Any]:
    """The ``arm_kind`` metadata of a treatment whose ``prepare()`` never returned."""
    t = getattr(treatment, "inner", treatment)  # a plugin wrapper
    subs = getattr(t, "treatments", None)  # a combined arm
    if isinstance(subs, list):
        return {"arm_kind": "combined",
                "treatments": [{"name": s.name, **_static_arm_kind(s)} for s in subs]}
    return {"arm_kind": getattr(t, "arm_kind", None)}



def _refuse_session_records(recs: Sequence[Record]) -> None:
    """Refuse to judge a session run: its last answer would be scored against its first question."""
    if any(r.raw.get("session_turns") for r in recs):
        raise ValueError(
            "Session records are measurement-only: the judge would score a session's last "
            "answer against its first question. Judge a single-turn run instead."
        )


class ArmRunner:
    """Generate answers for each treatment arm and (optionally) judge them."""

    # Pure rollups over the answer records, kept in eval/arm_summary.py so this
    # module stays under the repo's 500-line ceiling. Re-attached here so
    # `self._cost_summary(...)` and existing tests keep working unchanged.
    _summarize_agentic_usage = staticmethod(summarize_agentic_usage)
    _cost_summary = staticmethod(cost_summary)

    def __init__(
        self,
        treatments: List[Treatment],
        model: str = DEFAULT_ANSWER_MODEL,
        provider: str = DEFAULT_ANSWER_PROVIDER,
        api_key: Optional[str] = None,
        max_iterations: int = 6,
        harness: str = "arms-runner",
        plugin_set: Optional[Dict[str, Any]] = None,
        matrix_cell: Optional[str] = None,
        max_tokens: Optional[int] = None,
        grounded_from_topics: bool = False,
        stats_correction: str = "holm",
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        session_turns: Optional[int] = None,
        turn_reminder: Optional[str] = None,
    ):
        if not treatments:
            raise ValueError("ArmRunner requires at least one treatment arm")
        if session_turns is not None and session_turns < 2:
            raise ValueError(
                f"session_turns must be at least 2 (one turn is the plain run), got {session_turns}"
            )
        if turn_reminder is not None and (session_turns is None or not turn_reminder.strip()):
            raise ValueError("turn_reminder needs session_turns and non-blank text")
        # Rejected here rather than at summarize time: the correction is only
        # applied after every arm has been answered and judged, so a typo would
        # otherwise surface after the whole run has been paid for.
        if stats_correction not in arm_stats.CORRECTIONS:
            raise ValueError(
                f"stats_correction must be one of {arm_stats.CORRECTIONS}, "
                f"got {stats_correction!r}"
            )
        self.treatments = treatments
        self.stats_correction = stats_correction
        self.model = model
        self.provider = provider
        self.api_key = api_key
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.seed = seed
        self.session_turns = session_turns
        self.turn_reminder = turn_reminder
        self.grounded_from_topics = grounded_from_topics
        self.harness = harness
        self.matrix_cell = matrix_cell
        # Treatment material per (question, arm), handed from answering to
        # judging without going through the persisted record. See
        # :mod:`dokimi.eval.judge_context`.
        self.judge_context = JudgeContextStore()
        self.plugin_set = plugin_set or {
            "plugin_set": "none",
            "plugin_set_id": "sha256:e3b0c44298fc1c149afbf4c8996fb924",
            "plugins": [],
        }

    @property
    def arm_names(self) -> List[str]:
        names = [t.name for t in self.treatments]
        if self.turn_reminder is not None:
            names += [name + REMINDER_ARM_SUFFIX for name in names]
        return names

    def _run_config(self) -> Dict[str, Any]:
        """Request-shaping settings, recorded so a run stays interpretable later.

        Two runs of the same questions under the same model are not comparable if
        they used different budgets: ``max_tokens`` decides whether an answer was
        truncated, and ``max_iterations`` decides whether an agentic arm ran out
        of tool-call rounds before answering. Without these, a truncated run and a
        complete one are indistinguishable in the artifact.

        ``temperature`` and ``seed`` are recorded for the same reason: sampling is
        non-deterministic by default, so a pinned run and a free-sampling one are
        differently reproducible and should not be silently pooled as replicates.
        """
        config: Dict[str, Any] = {
            "max_tokens": self.max_tokens,
            "max_iterations": self.max_iterations,
            "grounded_from_topics": self.grounded_from_topics,
            "temperature": self.temperature,
            "seed": self.seed,
        }
        # A session run and a single-turn run answer different questions of the model (the
        # history grows), so the pairing checks must be able to tell them apart.
        if self.session_turns is not None:
            config["session_turns"] = self.session_turns
            config["turn_reminder"] = self.turn_reminder
        return config

    def run(
        self,
        library_name: str,
        questions: List[Dict[str, Any]],
        library_id: Optional[str] = None,
        concurrency: int = 5,
    ) -> List[Dict[str, Any]]:
        """Answer every question under every arm. Returns one record per question."""
        n = len(questions)
        if self.session_turns is not None:
            # Before any answer is paid for: a question short of follow-ups fails the run.
            for i, q in enumerate(questions):
                qid = question_id(q)
                session_turns_of(q, question_text_of(q), self.session_turns,
                                 label=f"#{i}" if qid is None else str(qid))
        results: Dict[int, Dict[str, Any]] = {}
        lock = threading.Lock()
        done = [0]

        def _process(idx: int, q: Dict[str, Any]) -> None:
            rec = self._answer_question(library_name, library_id, q)
            with lock:
                results[idx] = rec
                done[0] += 1
                print(f"[{done[0]}/{n}] {rec['question_id']} OK", flush=True)

        print(f"Running {len(self.treatments)} arms over {n} questions "
              f"(arms: {', '.join(self.arm_names)})", flush=True)
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(_process, i, q) for i, q in enumerate(questions)]
            for f in as_completed(futures):
                f.result()

        return [results[i] for i in range(n)]

    def _answer_question(
        self, library_name: str, library_id: Optional[str], q: Dict[str, Any]
    ) -> Dict[str, Any]:
        # Same key order and blank handling as the judge's check, so a record that passes
        # the up-front check is answered with the text the judge will see.
        question_text = question_text_of(q)
        qid = question_id(q)
        if qid is None:
            qid = "unknown_q"

        turns = None
        if self.session_turns is not None:
            turns = session_turns_of(q, question_text, self.session_turns, label=qid)

        arms: Dict[str, Any] = {}
        for treatment in self.treatments:
            if turns is None:
                arms[treatment.name] = self._answer_one_arm(
                    treatment, question_text, library_name, library_id, qid,
                    verbatim=q.get("prompt_style") == "verbatim",
                )
                continue
            arms[treatment.name] = self._answer_session_arm(
                treatment, turns, library_name, library_id, qid
            )
            if self.turn_reminder is not None:
                arms[treatment.name + REMINDER_ARM_SUFFIX] = self._answer_session_arm(
                    treatment, turns, library_name, library_id, qid,
                    reminder=self.turn_reminder,
                )

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
        if not ground_truth and self.grounded_from_topics:
            ground_truth = _ground_truth_from_topics(q)
        if ground_truth:
            rec["ground_truth_chunk"] = ground_truth
        return rec

    def _answer_one_arm(
        self,
        treatment: Treatment,
        question_text: str,
        library_name: str,
        library_id: Optional[str],
        question_id: str = "unknown_q",
        verbatim: bool = False,
    ) -> Dict[str, Any]:
        t0 = time.time()
        try:
            cfg = treatment.prepare(question_text, library_name, library_id)
        except Exception as exc:
            logger.exception("Arm %s prepare() failed", treatment.name)
            # The arm kind still says what failed, so a failure-counting summary
            # can attribute it (there is no config to read it from).
            return {"error": f"prepare_failed: {exc}", "answer": None,
                    "metadata": _static_arm_kind(treatment)}

        # Record the treatment material before the answer exists: this is what
        # the arm was given, and the judge needs all of it, not the 200-char
        # report snippets built further down. An agentic arm's answering path
        # does not put `injected_context` in the prompt (only question, tools
        # and system prompt reach the loop), so the judge gets none of it
        # either — a combined `skill:x+skill_agent:y` arm would otherwise show
        # the judge a skill body the answerer never received.
        # A catalog's material does not fit the judge: its reference is built
        # from what the agent loaded, so it can only be stored after the loop.
        deferred = cfg.is_agentic and cfg.judge_reference_for_loaded is not None
        if not deferred:
            self.judge_context.put(
                question_id,
                treatment.name,
                build_judge_context([] if cfg.is_agentic else cfg.injected_context,
                                    cfg.judge_reference,
                                    injected_chars=MAX_CONTEXT_CHARS),
            )

        from dokimi.plugins import take_output_shapers
        shapers, cfg_metadata = take_output_shapers(cfg)

        if cfg.is_agentic:
            out = self._answer_agentic_arm(cfg, question_text, t0, shapers, cfg_metadata)
            if deferred:
                loaded = ((out.get("tool_usage") or {}).get("skill") or {}).get("loaded") or []
                assert cfg.judge_reference_for_loaded is not None
                self.judge_context.put(
                    question_id, treatment.name,
                    build_judge_context([], cfg.judge_reference_for_loaded(loaded)),
                )
            return out

        cache_prefix = None
        if cfg.has_context:
            context = join_injected(cfg.injected_context)
            prefix = CONTEXT_PROMPT_PREFIX.format(context=context[:MAX_CONTEXT_CHARS])
            # A verbatim (imported) prompt follows the context unchanged, as `answers generate` sends it.
            prompt = question_text if verbatim else CONTEXT_PROMPT_SUFFIX.format(question=question_text)
            if cfg.context_is_static:
                # Hand the halves over separately so the provider can cache the
                # first one; what the model sees is the same concatenation.
                cache_prefix = prefix
            else:
                prompt = prefix + prompt
        else:
            prompt = question_text if verbatim else ANSWER_PROMPT_PLAIN.format(question=question_text)

        try:
            answer_text, usage = llm_call_with_usage(
                prompt=prompt,
                model=self.model,
                provider=self.provider,
                api_key=self.api_key,
                system=cfg.system_prompt,
                max_tokens=self.max_tokens,
                cache_prefix=cache_prefix,
                temperature=self.temperature,
                seed=self.seed,
            )
        except Exception as exc:
            logger.exception("Arm %s answer generation failed", treatment.name)
            return {"error": f"generation_failed: {exc}", "answer": None,
                    "metadata": cfg_metadata}

        raw_chars = len(answer_text or "")
        final_text = self._apply_shapers(shapers, answer_text)
        final_chars = len(final_text or "")

        rec = to_record(usage, self.model, self.provider)
        return {
            "answer": final_text,
            "raw_answer": answer_text if shapers else None,
            "model": self.model,
            "harness": self.harness,
            "plugin_set": self.plugin_set["plugin_set"],
            "plugin_set_id": self.plugin_set["plugin_set_id"],
            "used_system_prompt": bool(cfg.system_prompt),
            "context_chunks": [
                {
                    "source": c.get("source", "unknown"),
                    "snippet": (c["content"][:200] + "…") if len(c["content"]) > 200 else c["content"],
                    "relevance_score": c.get("relevance_score"),
                }
                for c in cfg.injected_context
            ],
            "token_usage": rec.as_token_usage_dict(),
            "metrics": rec.as_metrics_dict(answer_chars=raw_chars, final_chars=final_chars),
            "metadata": cfg_metadata,
            "elapsed_sec": round(time.time() - t0, 2),
        }

    def _answer_session_arm(
        self,
        treatment: Treatment,
        turns: List[str],
        library_name: str,
        library_id: Optional[str],
        question_id: str,
        reminder: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Answer one arm's multi-turn session; see :mod:`dokimi.eval.session`."""
        t0 = time.time()
        try:
            cfg = treatment.prepare(turns[0], library_name, library_id)
        except Exception as exc:
            logger.exception("Arm %s prepare() failed", treatment.name)
            return {"error": f"prepare_failed: {exc}", "answer": None,
                    "metadata": _static_arm_kind(treatment)}

        from dokimi.plugins import take_output_shapers
        shapers, cfg_metadata = take_output_shapers(cfg)
        if cfg.is_agentic:
            return {"error": "session_unsupported: an agentic arm (tools) is answered in one "
                             "turn only", "answer": None, "metadata": cfg_metadata}

        # The opening message is the single-turn prompt, context included, so turn 1 of
        # a session is the answer the plain run would have given, cache breakpoint included.
        cache_prefix = None
        if cfg.has_context:
            context = join_injected(cfg.injected_context)
            prefix = CONTEXT_PROMPT_PREFIX.format(context=context[:MAX_CONTEXT_CHARS])
            opening = CONTEXT_PROMPT_SUFFIX.format(question=turns[0])
            if cfg.context_is_static:
                cache_prefix = prefix
            else:
                opening = prefix + opening
        else:
            opening = ANSWER_PROMPT_PLAIN.format(question=turns[0])

        session = run_session(
            model=self.model, provider=self.provider, api_key=self.api_key,
            opening=opening, follow_ups=turns[1:], system=cfg.system_prompt,
            max_tokens=self.max_tokens, temperature=self.temperature, seed=self.seed,
            reminder=reminder, cache_prefix=cache_prefix,
        )
        session_block = {
            "turns_requested": len(turns),
            "turns": session["turns"],
            "stopped_reason": session["stopped_reason"],
            "reminder": reminder is not None,
        }
        if session["error"] is not None:
            logger.error("Arm %s session failed for %s: %s", treatment.name,
                         question_id, session["error"])
            return {"error": f"generation_failed: {session['error']}", "answer": None,
                    **self._session_spend(session),
                    "session": session_block, "metadata": cfg_metadata}

        if session["stopped_reason"] == "empty_reply":
            # The session ended short of the turns asked for. Recording it as a success
            # would mix 2-turn and 3-turn sessions in one arm's totals; the usage stays.
            error = f"empty_reply: turn {len(session['turns']) - 1}"
            logger.error("Arm %s session stopped for %s: %s", treatment.name, question_id, error)
            return {"error": error, "answer": None, **self._session_spend(session),
                    "session": session_block, "metadata": cfg_metadata}

        raw_answer = session["answer"]
        final_text = self._apply_shapers(shapers, raw_answer)
        usage = session["usage"]
        return {
            "answer": final_text,
            "raw_answer": raw_answer if shapers else None,
            "model": self.model,
            "harness": self.harness,
            "plugin_set": self.plugin_set["plugin_set"],
            "plugin_set_id": self.plugin_set["plugin_set_id"],
            "used_system_prompt": bool(cfg.system_prompt),
            "context_chunks": [
                {
                    "source": c.get("source", "unknown"),
                    "snippet": (c["content"][:200] + "…") if len(c["content"]) > 200 else c["content"],
                    "relevance_score": c.get("relevance_score"),
                }
                for c in cfg.injected_context
            ],
            # Whole-session totals, so the cost and token rollups read this arm like any other.
            "token_usage": usage.as_token_usage_dict(),
            "metrics": usage.as_metrics_dict(
                answer_chars=len(raw_answer or ""), final_chars=len(final_text or "")),
            "session": session_block,
            "metadata": cfg_metadata,
            "elapsed_sec": round(time.time() - t0, 2),
        }

    @staticmethod
    def _session_spend(session: Dict[str, Any]) -> Dict[str, Any]:
        """Token usage and metrics of the turns a failed session did complete.

        The calls were paid for, so the cost rollup must count them; an arm without
        ``metrics`` is skipped by it. No answer-length keys are written: a failed
        session has no answer, and plugin-delta averages those keys over every arm
        that has a ``metrics`` block.
        """
        if not session["turns"]:
            return {}
        usage = session["usage"]
        return {"token_usage": usage.as_token_usage_dict(),
                "metrics": usage.as_metrics_dict()}

    @staticmethod
    def _apply_shapers(shapers, answer_text):
        """Run output shapers over a raw answer (no-op when there are none)."""
        if not shapers:
            return answer_text
        from dokimi.plugins import apply_output_shapers
        return apply_output_shapers(shapers, answer_text or "")

    def _answer_agentic_arm(
        self, cfg, question_text: str, t0: float, shapers=None, cfg_metadata=None
    ) -> Dict[str, Any]:
        """Run an arm whose treatment offers tools via the agent loop."""
        from dokimi.eval.agent_runner import _summarize_tool_usage, run_agent_loop

        shapers = shapers or []
        cfg_metadata = cfg_metadata if cfg_metadata is not None else cfg.metadata

        try:
            result = run_agent_loop(
                question=question_text,
                tools=cfg.tools,
                model=self.model,
                provider=self.provider,
                system_prompt=cfg.system_prompt,
                api_key=self.api_key,
                max_iterations=self.max_iterations,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                seed=self.seed,
            )
        except Exception as exc:
            logger.exception("Agentic arm failed")
            # Keep what was offered and loaded before the failure.
            tool_usage = getattr(exc, "tool_usage", None) or _summarize_tool_usage(cfg.tools, [])
            return {"error": f"agent_failed: {exc}", "answer": None,
                    "tool_usage": tool_usage, "metadata": cfg_metadata}

        raw_answer = result["answer"]
        final_answer = self._apply_shapers(shapers, raw_answer)
        rec = to_record(result.get("usage") or result["token_usage"], self.model, self.provider)
        metrics = rec.as_metrics_dict(
            answer_chars=len(raw_answer or ""), final_chars=len(final_answer or "")
        )
        per_turn = result.get("per_turn")
        if per_turn is not None:
            metrics["per_turn"] = per_turn
        return {
            "answer": final_answer,
            "raw_answer": raw_answer if shapers else None,
            "model": self.model,
            "harness": self.harness,
            "plugin_set": self.plugin_set["plugin_set"],
            "plugin_set_id": self.plugin_set["plugin_set_id"],
            "agentic": True,
            "used_system_prompt": bool(cfg.system_prompt),
            "context_chunks": [],
            "transcript": result["transcript"],
            "tool_call_count": result["tool_call_count"],
            "tool_usage": result.get("tool_usage", {}),
            "discoverability": result.get("tool_usage", {}).get("skill", {}),
            "iterations": result["iterations"],
            "stopped_reason": result["stopped_reason"],
            "token_usage": rec.as_token_usage_dict(),
            "metrics": metrics,
            "metadata": cfg_metadata,
            "elapsed_sec": round(time.time() - t0, 2),
        }

    # ── Judging ─────────────────────────────────────────────────────────
    def judge(
        self,
        judge,
        library_name: str,
        records: Sequence[RecordLike],
        baseline_arm: str = "baseline",
        concurrency: int = 5,
    ) -> List[Dict[str, Any]]:
        """Score each arm's answer with the given Judge; add per-arm deltas.

        Deltas are computed against *baseline_arm* when that arm is present.
        """
        def _score(rec: Record) -> Dict[str, Any]:
            scores: Dict[str, Any] = {}
            gt = rec.ground_truth
            for arm_name in arm_names(rec):
                answer = rec.answer(arm_name)
                if answer is None or not answer.text:
                    scores[arm_name] = None
                    continue
                context, source = self.judge_context.resolve_with_source(
                    rec.question_id, arm_name, dict(answer.raw)
                )
                try:
                    score = judge.score_answer(
                        library_name=library_name,
                        question=rec.question_text,
                        answer=answer.text,
                        context=context,
                        ground_truth=gt,
                    )
                except Exception as exc:
                    logger.exception("Judging arm %s failed", arm_name)
                    score = {"error": str(exc)}
                if isinstance(score, dict):
                    score = {**score, "judge_context_source": source}
                    digest = (self.judge_context.digest_of(rec.question_id, arm_name)
                              if source == SOURCE_STORED else None)
                    if digest:
                        # Which of `judge_materials` this score saw.
                        score["judge_context_sha256"] = digest
                scores[arm_name] = score

            deltas = {}
            base = scores.get(baseline_arm)
            base_agg = base.get("aggregate") if isinstance(base, dict) else None
            if base_agg is not None:
                for arm_name, s in scores.items():
                    if arm_name != baseline_arm and isinstance(s, dict) and s.get("aggregate") is not None:
                        deltas[arm_name] = round(s["aggregate"] - base_agg, 1)

            return {
                "question_id": rec.question_id,
                "question_text": rec.question_text,
                "category": rec.category,
                "difficulty": rec.difficulty,
                "scores": scores,
                "deltas_vs_baseline": deltas,
            }

        recs = as_records(records, source="answers", kind="answers")
        # Before any judge call: refuse, do not grade against "".
        require_question_texts(r.raw for r in recs)
        _refuse_session_records(recs)
        n = len(recs)
        out: Dict[int, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_score, r): i for i, r in enumerate(recs)}
            for f in as_completed(futures):
                idx = futures[f]
                out[idx] = f.result()
        return [out[i] for i in range(n)]

    def build_output(
        self,
        library_name: str,
        records: List[Dict[str, Any]],
        evaluations: Optional[List[Dict[str, Any]]] = None,
        baseline_arm: str = "baseline",
        judge: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Assemble the serialisable arms-comparison artifact."""
        out: Dict[str, Any] = {
            "schema_version": "arms.v1",
            "generated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "library_name": library_name,
            "model": self.model,
            "provider": self.provider,
            "harness": self.harness,
            "plugin_set": self.plugin_set["plugin_set"],
            "plugin_set_id": self.plugin_set["plugin_set_id"],
            "plugins": self.plugin_set["plugins"],
            "cell": Cell(self.model, self.harness, self.plugin_set["plugin_set"]).key,
            "arms": self.arm_names,
            "baseline_arm": baseline_arm,
            "total_questions": len(records),
            "run_config": self._run_config(),
            "grounded_from_topics": self.grounded_from_topics,
            "grounded_questions": sum(
                1 for r in records if r.get("ground_truth_chunk")
            ),
            "answers": records,
            "cost_summary": self._cost_summary(records),
            "agentic_usage_summary": self._summarize_agentic_usage(records),
            "skill_selection_summary": summarize_skill_selection(records),
        }
        if self.session_turns is not None:
            out["session"] = {"turns": self.session_turns, "turn_reminder": self.turn_reminder}
        # One copy of each judge context the arm records cite, written whether or
        # not the run was judged, so an answers-only artifact can be judged later.
        materials = cite_judge_materials(records, self.judge_context)
        if materials:
            out["judge_materials"] = materials
        if self.matrix_cell:
            out["matrix_cell"] = self.matrix_cell
        if evaluations is not None:
            out["evaluations"] = evaluations
            out["summary"] = self._summarize(evaluations, baseline_arm)
            sources = [
                s.raw.get("judge_context_source")
                for ev in as_records(evaluations, source="evaluations", kind="evaluations")
                for s in ev.scores.values()
            ]
            out["judge_context_sources"] = dict(sorted(Counter(
                s for s in sources if s is not None).items()))
            stamp = judge_context_stamp(sources)
            if stamp is not None:
                out["judge_context"] = stamp
            if stamp != JUDGE_CONTEXT_PROTOCOL:
                logger.warning(
                    "Judge context for this run: %s — %s. Answer and judge in one "
                    "process, with unique question ids, for treatment-material scores.",
                    out["judge_context_sources"],
                    "unstamped, so it pools only with pre-treatment-material runs"
                    if stamp is None else f"stamped {stamp!r}, which pools with nothing",
                )
            if judge is not None:
                out["judge_model"] = getattr(judge, "model", None)
                out["judge_provider"] = getattr(judge, "provider", None)
                out["judge_temperature"] = getattr(judge, "temperature", None)
                out["judge_prompt_sha256"] = getattr(judge, "prompt_sha256", None)
        # Judge-side token/cost telemetry, when a judge ran (mirrors the answer
        # cost_summary so a full run's spend is auditable from one artifact).
        if judge is not None and hasattr(judge, "_usage_totals"):
            with judge._usage_lock:
                t = dict(judge._usage_totals)
                resolved = sorted(getattr(judge, "_resolved_models", ()))
            t["cost_usd"] = round(t["cost_usd"], 6) if t["cost_known_calls"] else None
            out["judge_metrics"] = t
            if resolved:  # the model a CLI alias judge ran as, as eval score records it
                out["judge_resolved_models"] = resolved
        return out

    def _summarize(
        self, evaluations: List[Dict[str, Any]], baseline_arm: str
    ) -> Dict[str, Any]:
        per_arm: Dict[str, List[float]] = {name: [] for name in self.arm_names}
        for ev in as_records(evaluations, source="evaluations", kind="evaluations"):
            for arm_name in ev.scores:
                if (v := ev.score(arm_name)) is not None:
                    per_arm.setdefault(arm_name, []).append(float(v))

        summary: Dict[str, Any] = {"per_arm": {}}
        base_avg = None
        for arm_name, vals in per_arm.items():
            avg = round(sum(vals) / len(vals), 1) if vals else None
            summary["per_arm"][arm_name] = {"avg_aggregate": avg, "n": len(vals)}
            if arm_name == baseline_arm:
                base_avg = avg
        if base_avg is not None:
            for arm_name, stats in summary["per_arm"].items():
                avg = stats["avg_aggregate"]
                stats["delta_vs_baseline"] = (
                    round(avg - base_avg, 1) if avg is not None and arm_name != baseline_arm else None
                )

        # Paired statistics. Every arm is tested against the *same* baseline
        # sample question-by-question, so the arms are mutually comparable and
        # p-values get corrected across the family. Unpaired means above are
        # kept as-is for artifact compatibility.
        arm_names = list(summary["per_arm"].keys())
        significance = arm_stats.arm_significance(
            evaluations, baseline_arm, arm_names, correction=self.stats_correction
        )
        for arm_name, sig in significance.items():
            summary["per_arm"].setdefault(arm_name, {})["significance"] = sig

        # Interaction contrasts are their own hypothesis family, corrected
        # among themselves rather than pooled with the arm comparisons above.
        interactions = interaction_significance(
            evaluations, arm_names, baseline_arm, correction=self.stats_correction
        )
        if interactions:
            summary["interactions"] = interactions

        return summary

    @staticmethod
    def save(output: Dict[str, Any], path: Path) -> None:
        save_artifact("arms", output, path)
        logger.info("Saved arms comparison to %s", path)
