# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""RAGAS meta-evaluation layer for dokimi.

Computes reference-free metrics on top of existing answer pairs:
  - faithfulness:        Does the with_docs answer stay faithful to the retrieved context?
  - answer_relevancy:    Is the answer relevant to the question? (both modes)
  - context_precision:   Is the retrieved context actually useful (high signal, low noise)?
  - context_recall:      Did the retrieved context cover what was needed?
                         (only when ground_truth_chunk is available)

Usage:
    from dokimi.eval.ragas_eval import RagasEvaluator

    evaluator = RagasEvaluator()   # the default judge (dokimi.defaults)
    results = evaluator.evaluate(answers)   # answers from Answerer.generate_answers()
    print(results.summary)
"""

import logging
import os
from typing import List, Dict, Any, Optional

from dokimi.eval.question_text import require_question_text
from dokimi.defaults import DEFAULT_JUDGE_MODEL, DEFAULT_JUDGE_PROVIDER
from dokimi.eval.records import BASELINE, CONTEXT, RecordLike, records

logger = logging.getLogger(__name__)


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
            api_key:    Optional API key (falls back to env var).
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
                              "Install: pip install ragas datasets") from exc

        rows_with = []
        rows_without: List[Dict[str, Any]] = []

        for i, rec in enumerate(records(answers, source="answers", kind="answers")):
            q = rec.question_text

            # --- CONTEXT ARM ---
            wd = rec.answer(CONTEXT)
            if wd is not None and wd.text and wd.context:
                # Rows are all built before the first RAGAS call: refuse, do not grade against "".
                require_question_text(rec.raw, i)
                row: Dict[str, Any] = {
                    "question": q,
                    "answer": wd.text,
                    "contexts": wd.context,
                    "question_id": rec.question_id,
                }
                # Add ground truth if available (enables context_recall)
                if rec.ground_truth and "context_recall" in self._requested:
                    row["ground_truth"] = rec.ground_truth
                rows_with.append(row)

            # --- BASELINE (answer_relevancy only) ---
            if include_without_docs and "answer_relevancy" in self._requested:
                wod = rec.answer(BASELINE)
                if wod is not None and wod.text:
                    require_question_text(rec.raw, i)
                    rows_without.append({
                        "question": q,
                        "answer": wod.text,
                        "contexts": [""],   # RAGAS requires contexts field
                        "question_id": rec.question_id,
                    })

        # Evaluate with_docs
        with_scores: Dict[str, Dict] = {}
        summary_with: Dict[str, float] = {}
        if rows_with:
            ds = Dataset.from_list(rows_with)
            active_metrics = self._metric_objects  # all requested metrics
            try:
                result = ragas_evaluate(
                    ds,
                    metrics=active_metrics,
                    llm=self._ragas_llm,
                    raise_exceptions=False,
                )
                df = result.to_pandas()
                for idx, row_data in enumerate(rows_with):
                    qid = row_data["question_id"]
                    with_scores[qid] = {
                        col: float(df.iloc[idx][col])
                        for col in df.columns
                        if col not in ("question", "answer", "contexts", "ground_truth",
                                       "question_id")
                        and not df.iloc[idx][col] != df.iloc[idx][col]  # skip NaN
                    }
                # Aggregate averages
                for col in df.columns:
                    if col not in ("question", "answer", "contexts", "ground_truth",
                                   "question_id"):
                        vals = df[col].dropna().tolist()
                        if vals:
                            summary_with[col] = round(sum(vals) / len(vals), 4)
            except Exception as exc:
                logger.error("RAGAS with_docs evaluation failed: %s", exc)

        # Evaluate without_docs (answer_relevancy only)
        without_scores: Dict[str, Dict] = {}
        summary_without: Dict[str, float] = {}
        if rows_without:
            from ragas.metrics import answer_relevancy as ar_metric
            ar_metric.llm = self._ragas_llm
            ds_wod = Dataset.from_list(rows_without)
            try:
                result_wod = ragas_evaluate(
                    ds_wod,
                    metrics=[ar_metric],
                    llm=self._ragas_llm,
                    raise_exceptions=False,
                )
                df_wod = result_wod.to_pandas()
                for idx, row_data in enumerate(rows_without):
                    qid = row_data["question_id"]
                    without_scores[qid] = {
                        col: float(df_wod.iloc[idx][col])
                        for col in df_wod.columns
                        if col not in ("question", "answer", "contexts", "question_id")
                        and not df_wod.iloc[idx][col] != df_wod.iloc[idx][col]
                    }
                vals = df_wod["answer_relevancy"].dropna().tolist()
                if vals:
                    summary_without["answer_relevancy"] = round(sum(vals) / len(vals), 4)
            except Exception as exc:
                logger.error("RAGAS without_docs evaluation failed: %s", exc)

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

            key = self._resolve_key()

            chat = {"openai": ChatOpenAI, "anthropic": ChatAnthropic}
            if self.provider in chat:
                lc_llm = chat[self.provider](model=self.llm_model, api_key=key or None)
            else:
                # RAGAS 0.2 needs a LangChain chat model, built here for openai and
                # anthropic only. Any other provider (google/vertex/openrouter/bedrock)
                # falls back to the default judge, so the scores still come from a
                # model of a different family than the default answerer.
                logger.warning(
                    "RAGAS LLM: provider '%s' not directly supported; "
                    "falling back to %s/%s for judging.",
                    self.provider, DEFAULT_JUDGE_PROVIDER, DEFAULT_JUDGE_MODEL,
                )
                lc_llm = chat[DEFAULT_JUDGE_PROVIDER](model=DEFAULT_JUDGE_MODEL)

            return LangchainLLMWrapper(lc_llm)

        except ImportError as exc:
            # Name the module that actually failed: ragas itself can fail on a transitive
            # import (e.g. a langchain_community module that a newer release removed).
            raise ImportError(
                f"RAGAS evaluation could not import {exc.name or 'a dependency'}: {exc}. "
                "Install the locked dependencies: pip install -r requirements.lock.txt"
            ) from exc

    def _build_metrics(self) -> list:
        """Instantiate requested RAGAS metric objects."""
        from ragas.metrics import (
            faithfulness,
            answer_relevancy,
            context_precision,
        )
        mapping = {
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "context_precision": context_precision,
        }
        # context_recall needs ground_truth — add only if explicitly requested
        if "context_recall" in self._requested:
            from ragas.metrics import context_recall
            mapping["context_recall"] = context_recall

        metrics = [mapping[k] for k in self._requested if k in mapping]
        if not metrics:
            raise ValueError(f"No valid metrics in {self._requested}. "
                             "Choose from: faithfulness, answer_relevancy, "
                             "context_precision, context_recall.")
        # Attach LLM to each metric
        for m in metrics:
            m.llm = self._ragas_llm
        return metrics

    def _resolve_key(self) -> str:
        """Resolve API key from instance or env."""
        if self.api_key:
            return self.api_key
        env_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GEMINI_API_KEY",
        }
        return os.environ.get(env_map.get(self.provider, "OPENAI_API_KEY"), "")


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
