# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""RAGAS meta-evaluation layer for dokimi.

Computes metrics on top of existing answer pairs with ragas 0.3; all but context_recall
need no reference answer:
  - faithfulness:        Does the with_docs answer stay faithful to the retrieved context?
  - answer_relevancy:    Is the answer relevant to the question? (both modes)
  - context_precision:   Did the retrieved documents help reach the with_docs answer, the
                         useful ones ranked first? (ragas's LLMContextPrecisionWithoutReference)
  - context_recall:      Did the retrieved context cover the ground truth?
                         (only when requested, scored for answers with a ground_truth_chunk)

Usage:
    from dokimi.eval.ragas_eval import RagasEvaluator

    evaluator = RagasEvaluator()   # the default judge (dokimi.defaults)
    results = evaluator.evaluate(answers)   # answers from Answerer.generate_answers()
    print(results.summary)
"""

import importlib
import logging
import os
from typing import List, Dict, Any, Optional, Tuple

from dokimi.eval.question_text import require_question_text
from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER
from dokimi.llm import credential_env_var, resolve_api_key
from dokimi.records import BASELINE, CONTEXT, RecordLike, records

logger = logging.getLogger(__name__)

# dokimi metric name -> the ragas 0.3 class that computes it (ragas.metrics).
_METRICS = {
    "faithfulness": "Faithfulness",
    "answer_relevancy": "AnswerRelevancy",
    "context_precision": "LLMContextPrecisionWithoutReference",
    "context_recall": "ContextRecall",
}


def _key(provider: str, api_key: Optional[str] = None) -> Optional[str]:
    """*provider*'s key as ``llm.resolve_api_key`` reads it; ``None`` when none is set.

    Raises:
        ValueError: the ``file:`` key is unreadable or names an empty file. Given ``None``,
            langchain would read the variable itself and send the ``file:`` string.
    """
    key = resolve_api_key(provider, api_key)
    if key is None and (api_key or os.environ.get(credential_env_var(provider))):
        source = "api_key" if api_key else credential_env_var(provider)
        raise ValueError(f"{source} for provider {provider} names an empty key file")
    return key


def _import_ragas_runtime():
    """Import ragas + datasets at runtime.

    Runtime import avoids module-level cache poisoning in tests that monkeypatch
    langchain modules in sys.modules.
    """
    from datasets import Dataset
    from ragas import evaluate as ragas_evaluate
    return Dataset, ragas_evaluate


class RagasEvaluator:
    """Compute RAGAS meta-evaluation metrics on answer pairs.

    Wraps the ragas library to score answers produced by Answerer.
    Works with the existing answer format — no pipeline changes needed.

    Attributes:
        llm_model:  Model used as the RAGAS judge (default: DEFAULT_JUDGE_MODEL).
        provider:   Provider for the RAGAS judge LLM.
        metrics:    List of ragas Metric objects to compute.
    """

    # Metrics that require retrieved context (with_docs only)
    _WITH_DOCS_METRICS = ("faithfulness", "context_precision", "answer_relevancy")
    # Metrics that work without context
    _ANY_MODE_METRICS = ("answer_relevancy",)

    def __init__(
        self,
        llm_model: str = DEFAULT_JUDGE_MODEL,
        provider: str = DEFAULT_JUDGE_PROVIDER,
        api_key: Optional[str] = None,
        metrics: Optional[List[str]] = None,
    ):
        """
        Args:
            llm_model:  LLM used internally by RAGAS for scoring.
            provider:   Provider — "openai", "anthropic", or "google".
            api_key:    Optional API key (falls back to env var); either may be ``file:<path>``.
            metrics:    Which metrics to compute. Defaults to all supported ones.
                        Choices: "faithfulness", "answer_relevancy",
                                 "context_precision", "context_recall".
        """
        self.llm_model = llm_model
        self.provider = provider
        self.api_key = api_key

        # Default metric set (context_recall needs ground_truth, excluded by default)
        self._requested = set(metrics or ["faithfulness", "answer_relevancy", "context_precision"])

        self._ragas_llm = self._build_ragas_llm()
        self._metric_objects = self._build_metrics()
        # Kept here, not on ragas's module-level metric, which every evaluator shares.
        self._ragas_embeddings = (self._build_ragas_embeddings()
                                  if "answer_relevancy" in self._requested else None)

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def evaluate(
        self,
        answers: List[RecordLike],
        include_without_docs: bool = True,
    ) -> "RagasResult":
        """Run RAGAS evaluation on a list of answer pairs.

        Args:
            answers:               Output from Answerer.generate_answers().
            include_without_docs:  Also score without_docs answers on answer_relevancy.

        Returns:
            RagasResult with per-answer scores and aggregate summary.
        """
        try:
            Dataset, ragas_evaluate = _import_ragas_runtime()
        except ImportError as exc:
            raise ImportError("ragas and datasets packages are required. "
                              "If the ragas extra is not installed: pip install 'dokimi[ragas]'") from exc

        # ragas 0.3's column names. Recall rows go apart: ragas checks the columns of the first row only.
        rows_with: List[Dict[str, Any]] = []
        rows_recall: List[Dict[str, Any]] = []
        rows_without: List[Dict[str, Any]] = []
        recall = "context_recall" in self._requested

        for i, rec in enumerate(records(answers, source="answers", kind="answers")):
            q = rec.question_text

            # --- CONTEXT ARM ---
            wd = rec.answer(CONTEXT)
            if wd is not None and wd.text and wd.context:
                # Rows are all built before the first RAGAS call: refuse, do not grade against "".
                require_question_text(rec.raw, i)
                row: Dict[str, Any] = {
                    "user_input": q,
                    "response": wd.text,
                    "retrieved_contexts": wd.context,
                    "question_id": rec.question_id,
                }
                rows_with.append(row)
                if recall and rec.ground_truth:
                    rows_recall.append({**row, "reference": rec.ground_truth})

            # --- BASELINE (answer_relevancy only) ---
            if include_without_docs and "answer_relevancy" in self._requested:
                wod = rec.answer(BASELINE)
                if wod is not None and wod.text:
                    require_question_text(rec.raw, i)
                    rows_without.append({
                        "user_input": q,
                        "response": wod.text,
                        "question_id": rec.question_id,
                    })

        with_scores, summary_with = self._score(
            Dataset, ragas_evaluate, rows_with,
            [m for m in self._metric_objects if m.name != "context_recall"], "with_docs")
        recall_scores, summary_recall = self._score(
            Dataset, ragas_evaluate, rows_recall,
            [m for m in self._metric_objects if m.name == "context_recall"], "context_recall")
        for qid, scores in recall_scores.items():
            with_scores.setdefault(qid, {}).update(scores)
        summary_with.update(summary_recall)
        without_scores, summary_without = self._score(
            Dataset, ragas_evaluate, rows_without,
            [m for m in self._metric_objects if m.name == "answer_relevancy"], "without_docs")

        return RagasResult(
            with_docs_scores=with_scores,
            without_docs_scores=without_scores,
            summary_with_docs=summary_with,
            summary_without_docs=summary_without,
            n_with=len(rows_with),
            n_without=len(rows_without),
        )

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _build_ragas_llm(self):
        """Build a ragas-compatible LLM wrapper."""
        try:
            from ragas.llms import LangchainLLMWrapper
            from langchain_openai import ChatOpenAI
            from langchain_anthropic import ChatAnthropic
            from langchain_core.language_models import BaseChatModel
            lc_llm: BaseChatModel  # either provider's chat model

            chat = {"openai": ChatOpenAI, "anthropic": ChatAnthropic}
            if self.provider in chat:
                lc_llm = chat[self.provider](model=self.llm_model, api_key=_key(self.provider, self.api_key))
            else:
                # ragas 0.3 needs a LangChain chat model, built here for openai and anthropic only;
                # the default judge stays of another family than the default answerer.
                logger.warning(
                    "RAGAS LLM: provider '%s' not directly supported; "
                    "falling back to %s/%s for judging.",
                    self.provider, DEFAULT_JUDGE_PROVIDER, DEFAULT_JUDGE_MODEL,
                )
                lc_llm = chat[DEFAULT_JUDGE_PROVIDER](model=DEFAULT_JUDGE_MODEL,
                                                      api_key=_key(DEFAULT_JUDGE_PROVIDER))

            return LangchainLLMWrapper(lc_llm)

        except ImportError as exc:
            # Name the module that actually failed: ragas itself can fail on a transitive
            # import (e.g. a langchain_community module that a newer release removed).
            raise ImportError(
                f"RAGAS evaluation could not import {exc.name or 'a dependency'}: {exc}. "
                "If the ragas extra is not installed: pip install 'dokimi[ragas]'"
            ) from exc

    def _score(self, Dataset, ragas_evaluate, rows: List[Dict[str, Any]], metrics: list,
               arm: str) -> Tuple[Dict[str, Dict[str, float]], Dict[str, float]]:
        """Per-question scores and their means for one arm; empty when ragas fails (logged).

        Scores are read by the names of *metrics*: the result frame also holds the input
        columns, whose names ragas has changed before.
        """
        if not rows or not metrics:
            return {}, {}
        try:
            df = ragas_evaluate(
                Dataset.from_list(rows),
                metrics=metrics,
                llm=self._ragas_llm,
                embeddings=self._ragas_embeddings,
                raise_exceptions=False,
            ).to_pandas()
            names = [m.name for m in metrics if m.name in df.columns]
            scores = {
                row["question_id"]: {n: float(df.iloc[idx][n]) for n in names
                                     if df.iloc[idx][n] == df.iloc[idx][n]}  # skip NaN
                for idx, row in enumerate(rows)
            }
            summary = {n: round(sum(vals) / len(vals), 4)
                       for n in names if (vals := df[n].dropna().tolist())}
        except Exception as exc:  # noqa: BLE001 - logged; RAGAS is optional, the run keeps its own scores
            logger.error("RAGAS %s evaluation failed: %s", arm, exc)
            return {}, {}
        return scores, summary

    def _metric(self, name: str):
        """A new ragas 0.3 metric object for dokimi metric *name*, judged by this evaluator's LLM.

        context_precision is ragas's reference-free variant, named as dokimi names it: dokimi
        sends no reference answer to grade the context against.
        """
        ragas_metrics = importlib.import_module("ragas.metrics")
        return getattr(ragas_metrics, _METRICS[name])(name=name, llm=self._ragas_llm)

    def _build_metrics(self) -> list:
        """Instantiate requested RAGAS metric objects."""
        metrics = [self._metric(k) for k in sorted(self._requested) if k in _METRICS]
        if not metrics:
            raise ValueError(f"No valid metrics in {self._requested}. "
                             f"Choose from: {', '.join(_METRICS)}.")
        return metrics

    def _build_ragas_embeddings(self):
        """OpenAI embeddings for answer_relevancy, keyed as dokimi reads a key.

        ragas embeds with OpenAI whatever the judge, and its own default reads
        ``OPENAI_API_KEY`` as it is, a ``file:`` string included. ``None`` (no key) leaves
        that default.
        """
        key = _key("openai")
        if key is None:
            return None
        from langchain_openai import OpenAIEmbeddings
        from ragas.embeddings import LangchainEmbeddingsWrapper
        # ragas's default model (embedding_factory), so the scores do not move.
        return LangchainEmbeddingsWrapper(OpenAIEmbeddings(model="text-embedding-ada-002", openai_api_key=key))


class RagasResult:
    """Container for RAGAS evaluation output.

    Attributes:
        with_docs_scores:     {question_id: {metric: score}} for with_docs answers.
        without_docs_scores:  {question_id: {metric: score}} for without_docs answers.
        summary_with_docs:    Average scores across with_docs answers.
        summary_without_docs: Average scores across without_docs answers.
        n_with:               Number of with_docs answers evaluated.
        n_without:            Number of without_docs answers evaluated.
    """

    def __init__(
        self,
        with_docs_scores: Dict[str, Dict[str, float]],
        without_docs_scores: Dict[str, Dict[str, float]],
        summary_with_docs: Dict[str, float],
        summary_without_docs: Dict[str, float],
        n_with: int,
        n_without: int,
    ):
        self.with_docs_scores = with_docs_scores
        self.without_docs_scores = without_docs_scores
        self.summary_with_docs = summary_with_docs
        self.summary_without_docs = summary_without_docs
        self.n_with = n_with
        self.n_without = n_without

    def to_dict(self) -> Dict[str, Any]:
        """Serialisable dict for saving alongside answer JSON."""
        return {
            "ragas_summary": {
                "with_docs": self.summary_with_docs,
                "without_docs": self.summary_without_docs,
            },
            "ragas_n_evaluated": {
                "with_docs": self.n_with,
                "without_docs": self.n_without,
            },
            "ragas_per_question": {
                "with_docs": self.with_docs_scores,
                "without_docs": self.without_docs_scores,
            },
        }

    def format_summary(self) -> str:
        """Human-readable summary string."""
        lines = ["## RAGAS Meta-Evaluation\n"]

        lines.append(f"**Evaluated:** {self.n_with} context-arm · {self.n_without} baseline\n")

        if self.summary_with_docs:
            lines.append("### Context arm")
            for metric, score in sorted(self.summary_with_docs.items()):
                bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
                lines.append(f"  {metric:<22} {bar}  {score:.3f}")

        if self.summary_without_docs:
            lines.append("\n### Baseline (answer_relevancy only)")
            for metric, score in sorted(self.summary_without_docs.items()):
                bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
                lines.append(f"  {metric:<22} {bar}  {score:.3f}")

        if self.summary_with_docs and self.summary_without_docs:
            ar_with = self.summary_with_docs.get("answer_relevancy")
            ar_without = self.summary_without_docs.get("answer_relevancy")
            if ar_with is not None and ar_without is not None:
                delta = ar_with - ar_without
                sign = "+" if delta >= 0 else ""
                lines.append(f"\n  answer_relevancy delta (context arm minus baseline): {sign}{delta:.3f}")

        return "\n".join(lines)
