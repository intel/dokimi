# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Generate context-arm and baseline answers to questions using an LLM."""

import logging
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from dokimi.utils import question_id
from dokimi.artifacts import save_artifact
from dokimi.defaults import (
    ANSWERING_METHOD_VERSION,
    DEFAULT_ANSWER_MODEL,
    DEFAULT_ANSWER_PROVIDER,
    DEFAULT_RETRIEVAL_TOP_K,
)
from dokimi.llm import llm_call_with_usage, LANGCHAIN_AVAILABLE, check_caller_provider
from dokimi.metrics.usage import UsageRecord, to_record

from .reranker import SimpleReranker, SentenceTransformerReranker, SENTENCE_TRANSFORMERS_AVAILABLE


ANSWER_PROMPT_WITH_DOCS = """You are a technical documentation expert answering a user question.

**Question:** {question}

**Relevant documentation:**
{docs}

**Task:**
Answer the question using ONLY the provided documentation above. Be specific, accurate, and actionable. Include code examples if relevant.

**Answer:**"""


ANSWER_PROMPT_WITHOUT_DOCS = """You are a technical expert answering a user question based on your knowledge.

**Question:** {question}

**Task:**
Answer the question based on your training knowledge. Be specific and include code examples if relevant.

**Answer:**"""


#: Context arm for ``prompt_style: verbatim`` questions: docs first, then the prompt unchanged.
ANSWER_PROMPT_WITH_DOCS_VERBATIM = """Relevant documentation:
{docs}

---

{question}"""


def _ground_truth_fields(question: Dict[str, Any]) -> Dict[str, Any]:
    """The ground truth of a chunk-grounded question, for the answer record (none for other questions)."""
    if not question.get("ground_truth_chunk"):
        return {}
    return {"ground_truth_chunk": question["ground_truth_chunk"],
            "question_source": question.get("question_source", "chunk")}


def _terminated_block(stop_error: Optional[Exception], requested: Optional[int], recorded: int) -> Dict[str, Any]:
    """The ``terminated`` block of a run that a run-wide error stopped: the file says it is not the whole run."""
    if stop_error is None:
        return {}
    return {"terminated": {"reason": str(stop_error), "questions_requested": requested,
                           "questions_recorded": recorded}}


class Answerer:
    """
    Generate answers to questions using LLM.
    
    Supports two modes:
    - Context arm: retrieve docs via MCP, then answer with context.
    - Baseline: answer from LLM knowledge only.
    """
    
    #: The run's intent, stamped on the answers file (``knowledge`` or ``paired``); ``None`` is a legacy file.
    run_intent: Optional[str] = None

    def __init__(
        self,
        mcp_client=None,
        model: str = DEFAULT_ANSWER_MODEL,
        provider: str = DEFAULT_ANSWER_PROVIDER,
        api_key: Optional[str] = None,
        top_k: Optional[int] = None,
        rerank_threshold: float = 0.3,
        debug_retrieval: bool = False
    ):
        """
        Args:
            mcp_client: MCP client for context retrieval (e.g., Context7Client)
            model: LLM model name
            provider: "openai" or "anthropic"
            api_key: Optional API key
            top_k: Number of docs to retrieve before reranking (default DEFAULT_RETRIEVAL_TOP_K)
            rerank_threshold: Minimum relevance score (0-1) to keep docs
            debug_retrieval: If True, include detailed retrieval metadata in output
        """
        if not LANGCHAIN_AVAILABLE:
            raise ImportError(
                "LLM dependencies not available. "
                "Install: pip install litellm"
            )

        self.mcp_client = mcp_client
        self.model = model
        self.provider = provider
        self.api_key = api_key

        # The answer call goes through llm_call_with_usage, which picks the provider's client itself.
        check_caller_provider(provider)
        # None means the default: the doc source must never get max_results=None (it would return every chunk).
        self.top_k = DEFAULT_RETRIEVAL_TOP_K if top_k is None else top_k
        self.debug_retrieval = debug_retrieval
        
        # Initialize reranker (prefer semantic, fallback to lexical)
        self.reranker: SentenceTransformerReranker | SimpleReranker
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.reranker = SentenceTransformerReranker(threshold=rerank_threshold)
                logger.info(f"Semantic reranker initialized with threshold={rerank_threshold:.2f}")
            except Exception as e:  # noqa: BLE001 - logged; the lexical reranker is the documented fallback
                logger.warning(f"Semantic reranker failed ({e}); fallback to lexical reranker")
                self.reranker = SimpleReranker(threshold=rerank_threshold)
        else:
            self.reranker = SimpleReranker(threshold=rerank_threshold)
            logger.info(f"Lexical reranker initialized with threshold={rerank_threshold:.2f}")
        
        logger.info(f"Answerer initialized: {provider}/{model}, MCP={'yes' if mcp_client else 'no'}")
    
    def generate_answers(
        self,
        library_name: str,
        library_id: str,
        questions: List[Dict[str, Any]],
        max_tokens_per_question: int = 4000,
        output_path: Optional[Path] = None,
        concurrency: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Generate context-arm and baseline answers for all questions.

        Args:
            library_name: Library name (e.g., "oneTBB")
            library_id: Library ID for MCP (e.g., "uxlfoundation/oneTBB")
            questions: List of question dicts from QuestionGenerator
            max_tokens_per_question: Max tokens to retrieve per question
            output_path: If set, write incrementally after each question
            concurrency: Number of parallel API calls (default: 1 = sequential)

        Returns:
            List of answer dicts ordered by original question order.
        """
        n = len(questions)
        results: Dict[int, Dict] = {}   # idx → answer_pair
        lock = threading.Lock()
        completed = [0]

        stop = threading.Event()  # set by a failed save or a run-wide error: queued questions are skipped
        self.stop_error = None
        self.questions_requested = n

        def _process(idx: int, q: Dict) -> None:
            if stop.is_set():
                return
            q_id = question_id(q)
            q_id = str(idx) if q_id is None else q_id
            t0 = time.time()
            failure = None
            try:
                pair = self._generate_answer_pair(
                    library_name=library_name,
                    library_id=library_id,
                    question=q,
                    max_tokens=max_tokens_per_question
                )
            except Exception as e:  # noqa: BLE001 - the failure is stored on the pair and reported
                failure = e
                if getattr(e, "run_wide", False) is True:  # a CLI usage limit or login: queued questions are not paid for
                    stop.set()
                    self.stop_error = e  # the command exits nonzero after it saves what was answered
                done_conditions = getattr(e, "partial_conditions", None) or {}
                pair = {
                    "question_id": q_id,
                    "question_text": q.get("question") or q.get("text", ""),
                    "library_name": library_name,
                    "category": q.get("category"),
                    "difficulty": q.get("difficulty"),
                    "persona": q.get("persona"),
                    "error": str(e),
                    "with_docs": None,
                    "without_docs": None,
                    **done_conditions,
                    **_ground_truth_fields(q),  # a kept context answer is graded with its question's rubric
                }
            elapsed = time.time() - t0
            with lock:
                results[idx] = pair
                completed[0] += 1
                if failure is None:
                    print(f"[{completed[0]}/{n}] {q_id} ✓ ({elapsed:.1f}s)", flush=True)
                else:
                    print(f"[{completed[0]}/{n}] {q_id} ✗ ({elapsed:.1f}s): {failure}", flush=True)
                if output_path is not None:
                    ordered = [results[i] for i in sorted(results)]
                    try:
                        # Outside the generation try: a rejected save must not turn a paid answer
                        # into an error record, and must not be retried per question.
                        self._save_incremental(ordered, output_path)
                    except BaseException:
                        stop.set()
                        raise

        print(f"Generating answers for {n} questions (concurrency={concurrency})", flush=True)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_process, i, q): i for i, q in enumerate(questions)}
            for f in as_completed(futures):
                f.result()  # wait for completion

        answers = [results[i] for i in sorted(results)]
        self._print_summary(answers, n)
        return answers

    def _print_summary(self, answers: List[Dict[str, Any]], n: int) -> None:
        if len(answers) < n:
            print(f"  Stopped: {n - len(answers)} of {n} questions not asked (run-wide error)", flush=True)
        ok_with = sum(1 for a in answers if a.get("with_docs"))
        ok_without = sum(1 for a in answers if a.get("without_docs"))
        errors = sum(1 for a in answers if a.get("error"))

        print("\n✓ Generated answers:", flush=True)
        if self.mcp_client is not None:  # a knowledge run (--doc-source none) has no context arm to count
            print(f"  Context arm:  {ok_with}/{n}", flush=True)
        print(f"  Baseline:     {ok_without}/{n}", flush=True)
        if errors:
            print(f"  Errors:       {errors}", flush=True)
    
    def _generate_answer_pair(
        self,
        library_name: str,
        library_id: str,
        question: Dict[str, Any],
        max_tokens: int
    ) -> Dict[str, Any]:
        """Generate context-arm and baseline answers for a single question."""
        question_text = (
            question.get("question")
            or question.get("text")
            or question.get("question_text", "")
        )
        qid = question_id(question)
        qid = "unknown_q" if qid is None else qid
        # Imported eval formats grade the answer to the prompt as written (exact match,
        # "ANSWER: X"); the expert-answer wrapper would change what is asked.
        verbatim = question.get("prompt_style") == "verbatim"
        
        # Context arm
        with_docs_answer = None
        if self.mcp_client is not None:
            retrieved_docs, retrieval_metadata = self._retrieve_docs(
                library_id, question_text, max_tokens, return_metadata=True
            )
            
            if retrieved_docs:
                # Generate answer with retrieved context
                with_docs_answer = self._generate_with_docs(
                    question_text, retrieved_docs, retrieval_metadata,
                    verbatim=verbatim,
                )
            else:
                # Fallback: no relevant docs found
                logger.warning(
                    f"No relevant docs for {qid}, "
                    f"metadata: {retrieval_metadata}"
                )
                empty = UsageRecord(model=self.model, provider=self.provider,
                                    cost_usd=0.0, n_calls=0)
                token_usage = empty.as_token_usage_dict()
                token_usage["context_chars"] = 0
                with_docs_answer = {
                    "answer": "[FALLBACK: No relevant documentation found]",
                    "retrieved_docs": [],
                    "model": self.model,
                    "doc_source": "fallback_none",
                    "retrieval_metadata": retrieval_metadata if self.debug_retrieval else None,
                    "token_usage": token_usage,
                    "metrics": empty.as_metrics_dict(answer_chars=0),
                }
        else:
            logger.warning(f"No MCP client - skipping context arm for {qid}")
        
        # Baseline. A failure here keeps the context answer already paid for (with its telemetry).
        try:
            without_docs_answer = self._generate_without_docs(question_text, verbatim=verbatim)
        except Exception as exc:
            carrier: Any = exc  # the pair's completed condition rides on the error for the caller
            carrier.partial_conditions = {"with_docs": with_docs_answer, "without_docs": None}
            raise

        result = {
            "question_id": qid,
            "question_text": question_text,
            "library_name": library_name,
            "category": question.get("category"),
            "difficulty": question.get("difficulty"),
            "persona": question.get("persona"),
            "with_docs": with_docs_answer,
            "without_docs": without_docs_answer,
        }
        result.update(_ground_truth_fields(question))
        return result
    
    def _retrieve_docs(
        self,
        library_id: str,
        question: str,
        max_tokens: int,
        return_metadata: bool = False,
    ) -> Any:
        """
        Retrieve relevant docs via MCP client with reranking.
        
        Returns:
            (docs, metadata): Reranked docs and retrieval metadata dict
        """
        metadata = {
            "raw_count": 0,
            "after_rerank": 0,
            "top_score": None,
            "avg_score": None,
            "fallback_triggered": False
        }
        
        try:
            # Retrieve top-k docs, the same request the arms stack sends (max_results=top_k)
            raw_docs = self.mcp_client.get_library_docs(
                library_id=library_id,
                query=question,
                max_results=self.top_k,
                max_tokens=max_tokens
            )
            metadata["raw_count"] = len(raw_docs)
            logger.info(f"Retrieved {len(raw_docs)} raw doc chunks")
            
            if not raw_docs:
                return ([], metadata) if return_metadata else []
            
            # Rerank docs by relevance
            reranked_docs = self.reranker.rerank(question, raw_docs)
            metadata["after_rerank"] = len(reranked_docs)
            
            if reranked_docs:
                scores = [d.get('relevance_score', 0) for d in reranked_docs]
                metadata["top_score"] = round(max(scores), 3)
                metadata["avg_score"] = round(sum(scores) / len(scores), 3)
                
                # Keep top 3 after reranking
                docs_out = reranked_docs[:3]
                return (docs_out, metadata) if return_metadata else docs_out
            else:
                # No docs passed threshold
                metadata["fallback_triggered"] = True
                logger.warning("No docs passed rerank threshold for question")
                return ([], metadata) if return_metadata else []
                
        except Exception:
            logger.exception("Context retrieval failed")
            return ([], metadata) if return_metadata else []
    
    def _generate_with_docs(
        self,
        question: str,
        docs: List[Dict[str, Any]],
        retrieval_metadata: Optional[Dict[str, Any]] = None,
        verbatim: bool = False,
    ) -> Dict[str, Any]:
        """Generate context-arm answer. Called only with a non-empty *docs* (see ``_generate_answer_pair``)."""
        # ~4k tokens at ~4 chars/token; keeps prompt within most models' context window
        MAX_DOC_CHARS = 15_000

        # Format docs
        docs_text = "\n\n---\n\n".join(d["content"] for d in docs)
        context_chars = len(docs_text)
        docs_text_trimmed = docs_text[:MAX_DOC_CHARS]

        # Generate answer
        template = ANSWER_PROMPT_WITH_DOCS_VERBATIM if verbatim else ANSWER_PROMPT_WITH_DOCS
        prompt = template.format(question=question, docs=docs_text_trimmed)

        answer_text, usage = llm_call_with_usage(
            prompt=prompt,
            model=self.model,
            provider=self.provider,
            api_key=self.api_key,
        )

        rec = to_record(usage, self.model, self.provider)
        # Attach context size to the token_usage dict for analysis.
        token_usage = rec.as_token_usage_dict()
        token_usage["context_chars"] = context_chars

        result = {
            "answer": answer_text,
            "retrieved_docs": [
                {
                    "source": d.get("source", "unknown"),
                    "snippet": d["content"][:200] + "..." if len(d["content"]) > 200 else d["content"],
                    "relevance_score": d.get("relevance_score"),
                }
                for d in docs
            ],
            "model": self.model,
            "doc_source": docs[0].get("source", "unknown") if docs else "none",
            "token_usage": token_usage,
            "metrics": rec.as_metrics_dict(answer_chars=len(answer_text or "")),
        }

        # Add metadata if debug mode enabled
        if self.debug_retrieval and retrieval_metadata:
            result["retrieval_metadata"] = retrieval_metadata

        return result

    def _generate_without_docs(self, question: str, verbatim: bool = False) -> Dict[str, Any]:
        """Generate baseline answer (the bare prompt when ``verbatim``)."""
        prompt = question if verbatim else ANSWER_PROMPT_WITHOUT_DOCS.format(question=question)

        answer_text, usage = llm_call_with_usage(
            prompt=prompt,
            model=self.model,
            provider=self.provider,
            api_key=self.api_key,
        )

        rec = to_record(usage, self.model, self.provider)
        return {
            "answer": answer_text,
            "model": self.model,
            "token_usage": rec.as_token_usage_dict(),
            "metrics": rec.as_metrics_dict(answer_chars=len(answer_text or "")),
        }
    
    def _build_output(self, answers: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build the serialisable output structure for a set of answers."""
        # Aggregate token usage across all answers
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
        totals = dict.fromkeys(keys, 0)
        by_mode = {mode: dict.fromkeys(keys, 0) for mode in ("with_docs", "without_docs")}
        for a in answers:
            for mode, sums in by_mode.items():
                u = (a.get(mode) or {}).get("token_usage") or {}
                for k in keys:
                    v = u.get(k, 0) or 0
                    sums[k] += v
                    totals[k] += v
        total_usage = {**totals, **by_mode}

        return {
            "generated_at": self._get_timestamp(),
            "method_version": ANSWERING_METHOD_VERSION,
            **({"run_intent": self.run_intent} if self.run_intent else {}),
            "model": self.model,
            "provider": self.provider,
            "total_questions": len(answers),
            **_terminated_block(getattr(self, "stop_error", None), getattr(self, "questions_requested", None),
                                len(answers)),
            "token_usage_summary": total_usage,
            "answers": answers,
        }

    def _save_incremental(self, answers: List[Dict[str, Any]], output_path: Path) -> None:
        """Write current answers to disk atomically (called after each question)."""
        save_artifact("answers", self._build_output(answers), output_path)

    def save_answers(
        self,
        answers: List[Dict[str, Any]],
        output_path: Path,
        question_set_hash: Optional[str] = None,
    ):
        """Save answers to JSON file."""
        data = self._build_output(answers)
        if question_set_hash:
            data["question_set_hash"] = question_set_hash
        save_artifact("answers", data, output_path)
        logger.info(f"✓ Saved answers for {len(answers)} questions to {output_path}")
    
    @staticmethod
    def load_answers(input_path: Path) -> Dict[str, Any]:
        """Load answers from JSON file."""
        with open(input_path, 'r', encoding="utf-8") as f:
            return json.load(f)
    
    @staticmethod
    def _get_timestamp() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
