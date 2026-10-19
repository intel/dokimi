# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Judge answers using LLM-as-judge approach."""

import hashlib
import logging
import json
import math
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Iterable, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from dokimi.artifacts import save_artifact
from dokimi.llm import ChatModel
from dokimi.llm import ChatOpenAI, ChatAnthropic, LANGCHAIN_AVAILABLE
from dokimi.eval.diagnoser import diagnose, retrieval_hit
from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER
# One calibration scale for both the single judge and the panel, so their
# numbers mean the same thing and can be compared.
from dokimi.eval.panel import NUMERIC_CLAIMS, NUMERIC_INSTRUCTION, NUMERIC_OUTPUT, SCORE_ANCHORS

# The output format below uses N placeholders on purpose. Concrete example
# scores anchor the model: a prompt that shows 85/90/80/85/90 gets those exact
# values back for most items regardless of the answer, which collapses the
# usable range of the scale. Describe the levels, never demonstrate them.
_NO_EXAMPLE_SCORES = """- Replace every N with an integer 0-100, picked from the anchors above
- Score each dimension independently; do not round them toward each other"""

# The numeric-claims criterion, its enumeration step and output slot are shared
# with the panel and live next to the shared score anchors in `eval/panel.py`,
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

#: Appended to the prompt of a re-draw at pinned sampling, where the plain prompt
#: would return the same malformed reply. Numbered so every re-draw differs.
_FORMAT_REMINDER = (
    "\n\nReminder (re-draw {n} of 2): your previous reply could not be parsed. "
    "Reply with the JSON object only, in exactly the output format above, with "
    "every listed key and an integer for every score."
)

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
        answers: List[Dict[str, Any]],
        output_path: Optional[Path] = None,
        concurrency: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Evaluate all context-arm and baseline answers.

        Args:
            library_name: Library name for context
            answers: List of answer dicts from Answerer
            output_path: If set, write incrementally after each evaluation
            concurrency: Number of parallel judge calls (default: 1 = sequential)

        Returns:
            List of evaluation dicts ordered by input order.
        """
        n = len(answers)
        results: Dict[int, Dict] = {}
        lock = threading.Lock()
        completed = [0]

        stop = threading.Event()  # set by a failed save: queued answers are skipped, not paid for

        def _process(idx: int, answer: Dict) -> None:
            if stop.is_set():
                return
            q_id = answer["question_id"]
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
                    "question_id": answer["question_id"],
                    "question_text": answer["question_text"],
                    "category": answer.get("category"),
                    "difficulty": answer.get("difficulty"),
                    "persona": answer.get("persona"),
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
            futures = {pool.submit(_process, i, a): i for i, a in enumerate(answers)}
            for f in as_completed(futures):
                f.result()  # wait for completion

        evaluations = [results[i] for i in range(n)]
        valid = [e for e in evaluations if e.get("delta") is not None]
        if valid:
            avg_with = sum((e.get("with_docs") or {}).get("aggregate", 0) for e in valid) / len(valid)
            avg_without = sum((e.get("without_docs") or {}).get("aggregate", 0) for e in valid) / len(valid)
            avg_delta = sum(e["delta"] for e in valid) / len(valid)
            print("\n✓ Evaluation complete:", flush=True)
            print(f"  Context avg:      {avg_with:.1f}", flush=True)
            print(f"  Baseline avg:     {avg_without:.1f}", flush=True)
            print(f"  Average delta:    {avg_delta:.1f}", flush=True)
        return evaluations
    
    def _evaluate_answer_pair(
        self,
        library_name: str,
        answer: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Evaluate a single context-arm/baseline answer pair."""
        question_text = answer["question_text"]
        ground_truth = answer.get("ground_truth_chunk")  # present for chunk-grounded questions

        # Evaluate context arm
        with_docs_eval = None
        if answer.get("with_docs") and answer["with_docs"].get("answer"):
            context = self._format_context(answer["with_docs"].get("retrieved_docs", []))
            with_docs_eval = self._judge_answer(
                library_name=library_name,
                question=question_text,
                answer=answer["with_docs"]["answer"],
                context=context,
                ground_truth=ground_truth,
            )

        # Evaluate baseline
        without_docs_eval = None
        if answer.get("without_docs") and answer["without_docs"].get("answer"):
            without_docs_eval = self._judge_answer(
                library_name=library_name,
                question=question_text,
                answer=answer["without_docs"]["answer"],
                context="(No documentation provided)",
                ground_truth=ground_truth,
            )
        
        # Calculate delta
        delta = None
        if with_docs_eval and without_docs_eval:
            delta = with_docs_eval["aggregate"] - without_docs_eval["aggregate"]

        result = {
            "question_id": answer["question_id"],
            "question_text": question_text,
            "category": answer.get("category"),
            "difficulty": answer.get("difficulty"),
            "persona": answer.get("persona"),
            "with_docs": with_docs_eval,
            "without_docs": without_docs_eval,
            "delta": delta,
        }

        # Classify retrieval outcome (why docs helped or didn't)
        result["diagnosis"] = diagnose(answer, result)
        # Carried onto the eval record because the trust gate reads eval.v1, not
        # the answers it was judged from.
        result["retrieval_hit"] = retrieval_hit(answer)
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

    #: A verdict after a colon, a dash or an opening parenthesis, optionally
    #: qualified: "64 B: wrong", "64 B - wrong", "64 B (wrong)", "64 B: partially wrong".
    #: It is tried first, so every claim it reads keeps its reading.
    _VERDICT = re.compile(
        r"(?::|\s[-\u2013\u2014]|\()\s*(?:[a-z]+ly\s+)?(wrong|incorrect|right|correct)\b", re.I
    )
    #: Gate for the fallback: the claim must contain a ``:``, a dash after whitespace (``-``,
    #: en or em dash) or ``(`` somewhere (it may be inside the figure). The fallback searches the whole claim; its end-anchor, not a start
    #: offset, is what keeps a ``/`` or ``,`` in the figure from being read as the verdict.
    _VERDICT_PART = re.compile(r":|\s[-\u2013\u2014]|\(")
    #: Fallback for a claim ``_VERDICT`` cannot read. The verdict word must end the claim (only
    #: whitespace, ``.`` and ``)`` may follow) and come after a separator or a ``,``, either
    #: directly (``reasonable, not wrong``) or behind single-token ``/`` qualifiers that start
    #: right there (``misleading/wrong``, ``L2/wrong``). A ``/`` deeper in prose or a path
    #: (``see docs/wrong``, ``see section 4.2/wrong``) is not a verdict, so the claim stays
    #: unread. A path right after the separator (``ref: tests/wrong``) reads like ``L2/wrong``;
    #: only a path after a word, as in ``see docs/wrong``, stays unread. ``not`` flips it.
    #: A ``/`` or ``,`` right after another verdict word (``right/wrong``, ``correct, wrong``)
    #: is an alternative, not a verdict, so the claim stays unread; ``(?<!correct)`` also
    #: covers ``incorrect``. Only the intensifiers in ``_INTENSIFIERS`` may qualify the word
    #: here; any other ``-ly`` adverb (``hardly``, ``possibly``, ``formerly``) hedges or
    #: dates the verdict and leaves the claim unread. A negation is resolved before the
    #: intensifier: ``not entirely correct`` is wrong, but ``not entirely wrong`` hedges and
    #: stays unread. A verdict word with no separator in front stays unread on
    #: purpose: ``correctly marked as WRONG to claim`` says the answer flagged a bad claim,
    #: not that its figure is wrong.
    _QUALIFIED_VERDICT = re.compile(
        r"(?:(?::|\s[-\u2013\u2014]|\(|(?<!wrong)(?<!right)(?<!correct),)\s*"
        r"(?:(?!(?:wrong|right|correct|incorrect)/)[\w.+-]+/)*)"
        r"(not\s+)?(?:([a-z]+ly)\s+)?(wrong|incorrect|right|correct)[\s.)]*$",
        re.I,
    )
    #: Adverbs that strengthen a fallback verdict instead of hedging it.
    _INTENSIFIERS = frozenset({"partially", "mostly", "largely", "completely", "totally", "entirely"})

    @classmethod
    def _is_wrong_figure(cls, claim: Any) -> Optional[bool]:
        """True for a claim judged wrong, None for one with no readable verdict."""
        text = f": {claim.get('verdict')}" if isinstance(claim, dict) else claim
        if not isinstance(text, str):
            return None
        match = cls._VERDICT.search(text)
        if match is not None:
            return match.group(1).lower() in ("wrong", "incorrect")
        match = cls._QUALIFIED_VERDICT.search(text) if cls._VERDICT_PART.search(text) else None
        if match is None:
            return None
        negated, adverb, word = match.groups()
        if adverb and adverb.lower() not in cls._INTENSIFIERS:
            return None
        is_wrong_word = word.lower() in ("wrong", "incorrect")
        if adverb and negated and is_wrong_word:
            return None  # "not entirely wrong" hedges: partly right, or wrong
        if negated:
            return not is_wrong_word  # "not correct" is wrong; "not entirely correct" too
        return is_wrong_word

    @classmethod
    def _cap_for_wrong_figures(cls, scores: Dict[str, Any]) -> None:
        """Hold the prompt's promise: one wrong figure in `numeric_claims` caps correctness at 40.

        The judged score is kept under `caps`, so a capped 40 can be told from a judged 40,
        and the indexes of claims with no readable verdict under `unread_claims`.
        """
        claims = scores.get("numeric_claims")
        if not isinstance(claims, list):
            return
        verdicts = [cls._is_wrong_figure(c) for c in claims]
        unread = [i for i, wrong in enumerate(verdicts) if wrong is None]
        if unread:
            scores["unread_claims"] = unread
        if any(verdicts):
            judged = float(scores["correctness"])
            if judged > 40.0:
                scores["correctness"] = 40.0
                scores["caps"] = {"correctness": {"judged": judged, "rule": "wrong_figure"}}

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
        # A malformed reply is re-drawn without backoff. With judge sampling pinned
        # the same prompt returns the same reply, so a re-draw then appends a
        # format reminder. Each attempt is its own response-cache draw: with the
        # cache on, retrying under one key replayed the same unparseable reply
        # three times. Transport faults are retried by the transport layer, not
        # here, so a failed call raises at once.
        def parse(raw: str) -> "tuple[Dict[str, Any], float]":
            scores = extract_json_object(raw)
            missing = required - scores.keys()
            if missing:
                raise ValueError(f"Missing keys in judge response: {missing}")
            self._check_scores(scores, weights.keys() | {"specificity"})
            self._cap_for_wrong_figures(scores)
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
        last_exc: Exception = ValueError("judge retry loop did not execute")
        for attempt in range(3):
            attempt_prompt = prompt
            if attempt:
                if attempt == 1:
                    with self._usage_lock:
                        self._usage_totals["redrawn_judgements"] += 1
                if pinned:
                    attempt_prompt = prompt + _FORMAT_REMINDER.format(n=attempt)
            response = self.llm.invoke(attempt_prompt, draw=draw * 3 + attempt, cache_if=parses)
            raw = response.content if hasattr(response, "content") else str(response)
            self._accumulate_usage(getattr(response, "usage", None))
            try:
                scores, aggregate = parse(raw)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning("Judge response unparseable (attempt %d/3): %s", attempt + 1, exc)
        else:
            raise last_exc

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
    def _format_context(docs: List[Dict[str, Any]]) -> str:
        """Format retrieved docs as context string."""
        if not docs:
            return "(No documentation retrieved)"
        
        formatted = []
        for i, doc in enumerate(docs):
            snippet = doc.get("snippet", doc.get("content", ""))
            formatted.append(f"Doc {i+1}: {snippet}")
        
        return "\n".join(formatted)
    
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
