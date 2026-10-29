# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Judge answers using LLM-as-judge approach."""

import hashlib
import logging
import json
import math
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, List, Dict, Any, Iterable, Optional, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

from dokimi import caching
from dokimi.artifacts import save_artifact
from dokimi.llm import ChatModel, ask_parsed
from dokimi.llm import ChatOpenAI, ChatAnthropic, LANGCHAIN_AVAILABLE
from dokimi.eval.diagnoser import diagnose
from dokimi.eval.question_text import require_question_texts
from dokimi.records import BASELINE, CONTEXT, Record, RecordLike, records
from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER
# One calibration scale for both the single judge and the panel, so their
# numbers mean the same thing and can be compared.
from dokimi.eval.rubric import (
    NUMERIC_CLAIMS, NUMERIC_INSTRUCTION, NUMERIC_OUTPUT, SCORE_ANCHORS,
    cap_for_wrong_figures, reminder_reprompt,
)

# The output format below uses N placeholders on purpose. Concrete example
# scores anchor the model: a prompt that shows 85/90/80/85/90 gets those exact
# values back for most items regardless of the answer, which collapses the
# usable range of the scale. Describe the levels, never demonstrate them.
_NO_EXAMPLE_SCORES = """- Replace every N with an integer 0-100, picked from the anchors above
- Score each dimension independently; do not round them toward each other"""

# The numeric-claims criterion, its enumeration step and output slot are shared
# with the panel and live next to the shared score anchors in `eval/rubric.py`,
# with the measurements behind them.
_NUMERIC_CLAIMS = NUMERIC_CLAIMS
_NUMERIC_INSTRUCTION = NUMERIC_INSTRUCTION
_NUMERIC_OUTPUT = NUMERIC_OUTPUT


JUDGE_PROMPT = """You are evaluating the quality of an answer to a technical question.

**Question:** __QUESTION__

**Answer:** __ANSWER__

**Context (retrieved from docs, if available):**
__CONTEXT__

**Evaluation criteria:**

1. **Correctness (0-100):** Is the answer factually accurate?
""" + _NUMERIC_CLAIMS + """
2. **Completeness (0-100):** Does it fully address the question?
3. **Specificity (0-100):** Do the identifiers it names — APIs, functions,
   flags, tools — actually belong to __LIBRARY_NAME__ and fit this question?
   Naming __LIBRARY_NAME__ often is not specificity; a plausible-looking
   identifier that is wrong for __LIBRARY_NAME__ scores low.
4. **Code Quality (0-100):** If code is included, is it correct and runnable?
5. **Actionability (0-100):** Can the user apply this immediately?
""" + SCORE_ANCHORS + """
**Instructions:**
""" + _NO_EXAMPLE_SCORES + """
""" + _NUMERIC_INSTRUCTION + """
- Use the full range: an answer with a real defect must land well below 90
- Python computes the aggregate — do NOT compute it yourself, set aggregate=0
- Provide brief reasoning for each score

**Output format (JSON only, no other text):**
{
""" + _NUMERIC_OUTPUT + """
  "correctness": N,
  "completeness": N,
  "specificity": N,
  "code_quality": N,
  "actionability": N,
  "aggregate": 0,
  "reasoning": {
    "correctness": "<one sentence>",
    "completeness": "<one sentence>",
    "specificity": "<one sentence>",
    "code_quality": "<one sentence>",
    "actionability": "<one sentence>"
  }
}"""

# Extended prompt used when a ground-truth chunk is available.
# Adds a factual_grounding dimension that checks alignment with doc text.
JUDGE_PROMPT_GROUNDED = """You are evaluating the quality of an answer to a technical question.
The question was generated from the documentation excerpt below — use it as ground truth.

**Question:** __QUESTION__

**Answer:** __ANSWER__

**Ground-truth documentation excerpt (source of truth):**
```
__GROUND_TRUTH__
```

**Context (retrieved from docs, if available):**
__CONTEXT__

**Evaluation criteria:**

1. **Correctness (0-100):** Is the answer factually accurate compared to the ground-truth excerpt?
""" + _NUMERIC_CLAIMS + """
2. **Completeness (0-100):** Does it fully address the question using information from the excerpt?
3. **Specificity (0-100):** Do the identifiers it names — APIs, functions,
   flags, tools — actually belong to __LIBRARY_NAME__ and fit this question?
   Naming __LIBRARY_NAME__ often is not specificity; a plausible-looking
   identifier that is wrong for __LIBRARY_NAME__ scores low.
4. **Code Quality (0-100):** If code is included, is it correct and consistent with the excerpt?
5. **Actionability (0-100):** Can the user apply this immediately?
6. **Factual Grounding (0-100):** Does the answer stay faithful to the ground-truth excerpt?
   - 100: All facts match the excerpt exactly
   - 80: Minor omissions, no contradictions
   - 60: Some facts correct, some missing or vague
   - 40: Answer contradicts or ignores key information from excerpt
   - 0: Answer is completely inconsistent with the excerpt
""" + SCORE_ANCHORS + """
**Instructions:**
""" + _NO_EXAMPLE_SCORES + """
""" + _NUMERIC_INSTRUCTION + """
- Use the full range: an answer with a real defect must land well below 90
- Python computes the aggregate — do NOT compute it yourself, set aggregate=0
- Provide brief reasoning per dimension

**Output format (JSON only, no other text):**
{
""" + _NUMERIC_OUTPUT + """
  "correctness": N,
  "completeness": N,
  "specificity": N,
  "code_quality": N,
  "actionability": N,
  "factual_grounding": N,
  "aggregate": 0,
  "reasoning": {
    "correctness": "...",
    "completeness": "...",
    "specificity": "...",
    "code_quality": "...",
    "actionability": "...",
    "factual_grounding": "..."
  }
}"""

#: Identity of the judge prompts, stamped on judged artifacts: a prompt edit is a different judge.
JUDGE_PROMPT_SHA256 = "sha256:" + hashlib.sha256(
    (JUDGE_PROMPT + "\0" + JUDGE_PROMPT_GROUNDED).encode("utf-8")
).hexdigest()

MAX_GROUND_TRUTH_CHARS = 4000

#: Providers ``Judge.__init__`` accepts; CLI ``--judge-provider`` choices read this.
JUDGE_PROVIDERS = ("openai", "anthropic", "google", "google-vertex", "openrouter", "amazon-bedrock")


def _clip_ground_truth(ground_truth: Optional[str], limit: int = MAX_GROUND_TRUTH_CHARS) -> str:
    """Bound ground-truth length while preserving line structure.

    ``textwrap.shorten`` collapses all whitespace, which would flatten a
    multi-statement ground truth into one run-on line and make it far harder for
    the judge to check facts individually. Clip whole lines instead.
    """
    text = (ground_truth or "").strip()
    if len(text) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        if used + len(line) + 1 > limit:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept) + "\n…" if kept else text[:limit] + "…"


class Judge:
    """
    Evaluate answer quality using LLM-as-judge.
    
    Scores answers on 5 dimensions (0-100):
    - Correctness
    - Completeness
    - Specificity
    - Code Quality
    - Actionability
    """
    
    def __init__(
        self,
        model: str = DEFAULT_JUDGE_MODEL,
        provider: str = DEFAULT_JUDGE_PROVIDER,
        api_key: Optional[str] = None,
        run_metadata: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
        prompt_template: Optional[str] = None,
    ):
        """
        Args:
            model: LLM model for judging (should differ from answerer model)
            provider: "openai" or "anthropic"
            api_key: Optional API key
            temperature: Optional sampling temperature for the judge. Grading is
                a sampled call like any other, so leaving this unset leaves
                score variance on the table even when the answer is fixed.
            seed: Optional sampling seed, where the provider honours one.
            prompt_template: Replaces :data:`JUDGE_PROMPT` on the ungrounded
                path. Exists so the judge can be measured under a rewording of
                its own prompt; production scoring leaves it unset.
        """
        if not LANGCHAIN_AVAILABLE:
            raise ImportError(
                "LLM dependencies not available. "
                "Install: pip install litellm"
            )

        self.model = model
        self.provider = provider
        self.api_key = api_key
        self.temperature = temperature
        self.seed = seed
        self.prompt_sha256 = JUDGE_PROMPT_SHA256
        self.run_metadata = dict(run_metadata) if run_metadata else {}
        self.prompt_template = prompt_template
        # Accumulate judge-side token/cost telemetry across all judge calls
        # (Phase-C.0 covered answering; the judge path was untracked).
        self._usage_lock = threading.Lock()
        self._usage_totals = {
            "calls": 0, "cost_known_calls": 0, "cost_usd": 0.0,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "redrawn_judgements": 0,
        }

        self.llm: ChatModel
        if provider == "openai":
            self.llm = ChatOpenAI(model=model, api_key=api_key,
                                  temperature=temperature, seed=seed)
        elif provider == "anthropic":
            self.llm = ChatAnthropic(model=model, api_key=api_key,
                                     temperature=temperature, seed=seed)
        elif provider in ("google", "gemini", "google-vertex", "openrouter",
                          "amazon-bedrock", "vertex_ai"):
            from dokimi.utils import get_llm
            import os
            fallback_key = os.environ.get("OPENROUTER_API_KEY") if provider == "openrouter" else None
            self.llm = get_llm(provider, model, api_key or fallback_key,
                               temperature=temperature, seed=seed)
        else:
            raise ValueError(
                f"Unsupported provider: '{provider}'. "
                "Use 'openai', 'anthropic', 'google', 'openrouter', or 'amazon-bedrock'."
            )
        
        logger.info(f"Judge initialized: {provider}/{model}")
    
    def evaluate_answers(
        self,
        library_name: str,
        answers: Sequence[RecordLike],
        output_path: Optional[Path] = None,
        concurrency: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate all context-arm and baseline answers.

        Args:
            library_name: Library name for context
            answers: Answers records (from Answerer, or read by dokimi/records.py)
            output_path: If set, write incrementally after each evaluation
            concurrency: Number of parallel judge calls (default: 1 = sequential)

        Returns:
            List of evaluation dicts ordered by input order.

        Raises:
            RecordError: before any judge call, for a malformed answers record.
            EmptyQuestionError: before any judge call, for a record with an answer
                to judge and no question text (there is nothing to judge it against).
        """
        recs = records(answers, source="answers", kind="answers")
        # Before any judge call: refuse, do not grade against "".
        require_question_texts(r.raw for r in recs)
        n = len(recs)
        results: Dict[int, Dict] = {}
        lock = threading.Lock()
        completed = [0]

        stop = threading.Event()  # set by a failed save: queued answers are skipped, not paid for

        def _process(idx: int, answer: Record) -> None:
            if stop.is_set():
                return
            q_id = answer.question_id
            t0 = time.time()
            failure = None
            try:
                eval_result = self._evaluate_answer_pair(library_name, answer)
                delta = eval_result.get("delta")
                delta_str = f"delta={delta:+}" if delta is not None else "delta=n/a"
            except Exception as e:
                failure = e
                logger.exception(f"Evaluation failed for {q_id}")
                eval_result = {
                    "question_id": answer.question_id,
                    "question_text": answer.question_text,
                    "category": answer.category,
                    "difficulty": answer.difficulty,
                    "persona": answer.persona,
                    "error": str(e),
                    "with_docs": None,
                    "without_docs": None,
                    "delta": None
                }
            elapsed = time.time() - t0
            with lock:
                results[idx] = eval_result
                completed[0] += 1
                if failure is None:
                    print(f"[{completed[0]}/{n}] {q_id} ✓ {delta_str} ({elapsed:.1f}s)", flush=True)
                else:
                    print(f"[{completed[0]}/{n}] {q_id} ✗ ({elapsed:.1f}s): {failure}", flush=True)
                if output_path is not None:
                    ordered = [results[i] for i in sorted(results)]
                    try:
                        self._save_incremental(ordered, output_path)
                    except BaseException:
                        stop.set()
                        raise

        print(f"Evaluating {n} answers with judge: {self.provider}/{self.model} (concurrency={concurrency})", flush=True)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_process, i, a): i for i, a in enumerate(recs)}
            for f in as_completed(futures):
                f.result()  # wait for completion

        evaluations = [results[i] for i in range(n)]
        valid = [r for r in records(evaluations, kind="evaluations") if r.delta is not None]
        if valid:
            avg_with = sum(r.score(CONTEXT) or 0 for r in valid) / len(valid)
            avg_without = sum(r.score(BASELINE) or 0 for r in valid) / len(valid)
            avg_delta = sum(r.delta or 0 for r in valid) / len(valid)
            print("\n✓ Evaluation complete:", flush=True)
            print(f"  Context avg:      {avg_with:.1f}", flush=True)
            print(f"  Baseline avg:     {avg_without:.1f}", flush=True)
            print(f"  Average delta:    {avg_delta:.1f}", flush=True)
        return evaluations
    
    def _evaluate_answer_pair(
        self,
        library_name: str,
        item: RecordLike,
    ) -> Dict[str, Any]:
        """Evaluate a single context-arm/baseline answer pair."""
        answer = records([item], source="answers", kind="answers")[0]
        # Refused only when there is an answer to grade; an answer-error record keeps its row.
        require_question_texts([answer.raw])
        question_text = answer.question_text
        ground_truth = answer.ground_truth  # present for chunk-grounded questions
        context_answer = answer.answer(CONTEXT)
        baseline_answer = answer.answer(BASELINE)

        # Evaluate context arm
        with_docs_eval = None
        if context_answer is not None and context_answer.text:
            with_docs_eval = self._judge_answer(
                library_name=library_name,
                question=question_text,
                answer=context_answer.text,
                context=self._format_context(context_answer.context),
                ground_truth=ground_truth,
            )

        # Evaluate baseline
        without_docs_eval = None
        if baseline_answer is not None and baseline_answer.text:
            without_docs_eval = self._judge_answer(
                library_name=library_name,
                question=question_text,
                answer=baseline_answer.text,
                context="(No documentation provided)",
                ground_truth=ground_truth,
            )
        
        # Calculate delta
        delta = None
        if with_docs_eval and without_docs_eval:
            delta = with_docs_eval["aggregate"] - without_docs_eval["aggregate"]

        result = {
            "question_id": answer.question_id,
            "question_text": question_text,
            "category": answer.category,
            "difficulty": answer.difficulty,
            "persona": answer.persona,
            "with_docs": with_docs_eval,
            "without_docs": without_docs_eval,
            "delta": delta,
        }

        # Classify retrieval outcome (why docs helped or didn't)
        result["diagnosis"] = diagnose(context_answer, delta)
        # Carried onto the eval record because the trust gate reads eval.v1, not
        # the answers it was judged from.
        result["retrieval_hit"] = answer.retrieval_hit
        return result
    
    # Dimensions for standard (no ground truth) evaluation
    _STANDARD_DIMS = {"correctness", "completeness", "specificity", "code_quality", "actionability", "aggregate"}
    # Dimensions for grounded evaluation (adds factual_grounding)
    _GROUNDED_DIMS = _STANDARD_DIMS | {"factual_grounding"}

    #: Aggregate weights. `specificity` is asked for and reported but carries no
    #: weight, because it does not measure what the aggregate is for. Over 239
    #: scored answers the five dimensions behave as one factor wearing five
    #: labels (inter-dimension r = 0.47-0.93, corr(aggregate, actionability) =
    #: +0.97), so an equal-weight mean dilutes `correctness` — the only dimension
    #: that punishes a wrong answer — to a fifth of the score. Specificity is the
    #: worst offender to average in: it originated as a check for whether doc
    #: injection made answers mention Intel APIs, so it rewards dense,
    #: correctly-shaped identifiers even when every one of them is wrong, which
    #: is exactly the `wrong_tools` degradation. Dropping it and doubling
    #: correctness is what the judge-validity gate prefers on both judge models
    #: (see the PR for the measured numbers and their confidence interval).
    _STANDARD_WEIGHTS = {
        "correctness": 2.0, "completeness": 1.0, "code_quality": 1.0, "actionability": 1.0,
    }
    #: Grounded runs keep `factual_grounding` at the same weight as correctness —
    #: with an excerpt to check against, faithfulness to it *is* correctness.
    _GROUNDED_WEIGHTS = {**_STANDARD_WEIGHTS, "factual_grounding": 2.0}

    @staticmethod
    def _check_scores(scores: Dict[str, Any], dims: Iterable[str]) -> None:
        """Make each of *dims* in *scores* a float on the 0-100 scale, in place.

        A non-numeric value ("N/A", true, NaN, Infinity) is a formatting failure,
        so it raises and the reply is re-drawn. A numeric string ("7") is read as
        a number, and a finite number off the scale is clamped, as panel.py does.
        Every score is stored as a float, so 80 and "80" end up the same.
        """
        for dim in dims:
            value = scores[dim]
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError(f"Judge score {dim!r} is not a number: {value!r}")
            try:
                number = float(value)
            except ValueError:
                raise ValueError(f"Judge score {dim!r} is not a number: {value!r}") from None
            except OverflowError:  # an integer literal past the float range
                number = math.inf
            if not math.isfinite(number):
                raise ValueError(f"Judge score {dim!r} is not finite: {value!r}")
            scores[dim] = min(max(number, 0.0), 100.0)

    @staticmethod
    def _compute_aggregate(scores: Dict[str, Any], weights: Dict[str, float]) -> float:
        """Weighted mean of the scored dimensions — never trust the LLM to do math."""
        return round(
            sum(float(scores.get(dim, 0.0)) * w for dim, w in weights.items())
            / sum(weights.values()),
            1,
        )

    def _judge_answer(
        self,
        library_name: str,
        question: str,
        answer: str,
        context: str,
        ground_truth: Optional[str] = None,
        draw: int = 0,
    ) -> Dict[str, Any]:
        """Judge a single answer, optionally using a ground-truth doc chunk.

        ``draw`` selects an independent sample of the same judgement (see
        :func:`dokimi.llm.llm_call_with_usage`); 0 is the ordinary call.
        """
        from dokimi.llm import extract_json_object

        grounded = bool(ground_truth)

        if grounded:
            prompt = (
                JUDGE_PROMPT_GROUNDED
                .replace("__QUESTION__", question)
                .replace("__ANSWER__", answer)
                .replace("__CONTEXT__", context)
                .replace("__LIBRARY_NAME__", library_name)
                .replace("__GROUND_TRUTH__", _clip_ground_truth(ground_truth))
            )
        else:
            prompt = (
                (getattr(self, "prompt_template", None) or JUDGE_PROMPT)
                .replace("__QUESTION__", question)
                .replace("__ANSWER__", answer)
                .replace("__CONTEXT__", context)
                .replace("__LIBRARY_NAME__", library_name)
            )

        required = self._GROUNDED_DIMS if grounded else self._STANDARD_DIMS
        weights = self._GROUNDED_WEIGHTS if grounded else self._STANDARD_WEIGHTS

        # A malformed or truncated reply is a formatting failure, not a verdict:
        # without a retry the question loses its score for good and the report
        # still renders a clean-looking delta over the survivors.
        #
        # ask_parsed re-draws a malformed reply without backoff. With judge sampling
        # pinned the same prompt returns the same reply, so a re-draw then appends a
        # format reminder. Each attempt is its own response-cache draw. Transport
        # faults are retried by the transport layer, not here, so a failed call
        # raises at once.
        def parse(raw: str) -> "tuple[Dict[str, Any], float]":
            scores = extract_json_object(raw)
            missing = required - scores.keys()
            if missing:
                raise ValueError(f"Missing keys in judge response: {missing}")
            self._check_scores(scores, weights.keys() | {"specificity"})
            cap_for_wrong_figures(scores)
            return scores, self._compute_aggregate(scores, weights)

        def parses(raw: str) -> bool:
            # Only a reply the judge can use is cached. A stored unparseable one
            # is replayed on every later run, and once all three attempts are
            # stored the question has lost its score for good, without a call.
            try:
                parse(raw)
            except Exception:
                return False
            return True

        pinned = self.temperature is not None or self.seed is not None
        reprompt = reminder_reprompt(pinned)

        def attempt_text(attempt: int) -> str:
            return reprompt(prompt, attempt) if reprompt and attempt else prompt

        def send(text: str, n: int) -> str:
            response = self.llm.invoke(text, draw=n, cache_if=parses)
            self._accumulate_usage(getattr(response, "usage", None))
            return response.content if hasattr(response, "content") else str(response)

        # A rerun starts at the attempt whose reply is stored: the unparseable
        # attempts before it were never stored and would be asked live again.
        first, held = caching.first_replayed(
            3, lambda i: send(attempt_text(i), draw * 3 + i)) or (0, None)

        def ask(text: str, *, draw: int, cache_if: Callable[[str], bool]) -> str:
            attempt = draw % 3
            if attempt == max(first, 1):
                with self._usage_lock:
                    self._usage_totals["redrawn_judgements"] += 1
            if attempt == first and held is not None:
                return held
            return send(text, draw)

        (scores, aggregate), _ = ask_parsed(
            ask, prompt, parse, draw=draw, label="Judge response", start=first, reprompt=reprompt)

        # Python computes the aggregate on BOTH paths (the LLM sets aggregate=0
        # per instructions). Previously only the grounded path recomputed it and
        # the standard path shipped whatever number the model wrote, so the
        # headline metric was the judge's own arithmetic. It happened to match
        # the mean of its own dimensions on 239 scored answers (1 mismatch), so
        # this is a latent failure rather than a live one — but nothing enforced
        # it, and `panel.py` already holds the line the other way.
        scores["aggregate"] = aggregate
        if grounded:
            scores["grounded"] = True

        return scores
    
    def score_answer(
        self,
        library_name: str,
        question: str,
        answer: str,
        context: str = "(No documentation retrieved)",
        ground_truth: Optional[str] = None,
        draw: int = 0,
    ) -> Dict[str, Any]:
        """Public single-answer scorer — reused by the treatment-arm runner."""
        return self._judge_answer(
            library_name=library_name,
            question=question,
            answer=answer,
            context=context,
            ground_truth=ground_truth,
            draw=draw,
        )

    @staticmethod
    def _format_context(docs: List[str]) -> str:
        """Format the retrieved docs' text (``Answer.docs``) as the judge's context string."""
        if not docs:
            return "(No documentation retrieved)"
        return "\n".join(f"Doc {i+1}: {text}" for i, text in enumerate(docs))
    
    def _accumulate_usage(self, usage) -> None:
        """Fold one judge call's UsageRecord into the running totals (thread-safe).

        Defensive: a real UsageRecord has int token fields, but test doubles may
        carry non-numeric attributes — coerce and skip what does not convert so
        telemetry capture never breaks a judging run.
        """
        from dokimi.metrics.usage import UsageRecord
        if not isinstance(usage, UsageRecord):
            return

        def _int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        with self._usage_lock:
            t = self._usage_totals
            t["calls"] += 1
            t["prompt_tokens"] += _int(usage.prompt_tokens)
            t["completion_tokens"] += _int(usage.completion_tokens)
            t["total_tokens"] += _int(usage.total_tokens)
            cost = usage.cost_usd
            if isinstance(cost, (int, float)):
                t["cost_usd"] += float(cost)
                t["cost_known_calls"] += 1

    def _build_evaluation_output(self, evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build the serialisable output structure for a set of evaluations."""
        output: Dict[str, Any] = {
            "evaluated_at": self._get_timestamp(),
            "judge_model": self.model,
            "judge_provider": self.provider,
            "judge_temperature": self.temperature,
            "total_evaluations": len(evaluations),
            "evaluations": evaluations,
        }
        with self._usage_lock:
            totals = dict(self._usage_totals)
        # cost_usd is null (not 0.0) when no judge call had known pricing, so a
        # missing price reads distinct from a genuinely free judge.
        output["judge_metrics"] = {
            **totals,
            "cost_usd": round(totals["cost_usd"], 6) if totals["cost_known_calls"] else None,
        }
        if self.run_metadata:
            output["run_metadata"] = self.run_metadata
        return output

    def _save_incremental(self, evaluations: List[Dict[str, Any]], output_path: Path) -> None:
        """Write current evaluations to disk atomically (called after each question)."""
        save_artifact("eval", self._build_evaluation_output(evaluations), output_path)

    def save_evaluations(
        self,
        evaluations: List[Dict[str, Any]],
        output_path: Path
    ):
        """Save evaluations to JSON file."""
        save_artifact("eval", self._build_evaluation_output(evaluations), output_path)
        logger.info(f"✓ Saved evaluations for {len(evaluations)} answers to {output_path}")
    
    @staticmethod
    def load_evaluations(input_path: Path) -> Dict[str, Any]:
        """Load evaluations from JSON file."""
        with open(input_path, 'r', encoding="utf-8") as f:
            return json.load(f)
    
    @staticmethod
    def _get_timestamp() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def judge_ask(model: str, provider: str) -> Callable[..., str]:
    """The ``Ask`` of the reference checks (one prompt in, the reply text out) backed by a ``Judge``'s model."""
    llm = Judge(model=model, provider=provider).llm
    return lambda prompt, draw=0, cache_if=None: llm.invoke(prompt, draw=draw, cache_if=cache_if).content
