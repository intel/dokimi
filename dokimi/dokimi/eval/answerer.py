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

from dokimi.artifacts import save_artifact
from dokimi.llm import llm_call_with_usage, ChatOpenAI, ChatAnthropic, LANGCHAIN_AVAILABLE
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


class Answerer:
    """
    Generate answers to questions using LLM.
    
    Supports two modes:
    - Context arm: retrieve docs via MCP, then answer with context.
    - Baseline: answer from LLM knowledge only.
    """
    
    def __init__(
        self,
        mcp_client=None,
        model: str = "gpt-4o",
        provider: str = "openai",
        api_key: Optional[str] = None,
        top_k: int = 5,
        rerank_threshold: float = 0.3,
        debug_retrieval: bool = False
    ):
        """
        Args:
            mcp_client: MCP client for context retrieval (e.g., Context7Client)
            model: LLM model name
            provider: "openai" or "anthropic"
            api_key: Optional API key
            top_k: Number of docs to retrieve before reranking
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

        if provider == "openai":
            self.llm = ChatOpenAI(model=model, api_key=api_key)
        elif provider == "anthropic":
            self.llm = ChatAnthropic(model=model, api_key=api_key)
        elif provider in ("google", "gemini", "google-vertex", "openrouter",
                          "amazon-bedrock", "vertex_ai"):
            self.llm = None  # llm_call_with_usage handles these natively
        else:
            raise ValueError(
                f"Unsupported provider: '{provider}'. "
                "Use 'openai', 'anthropic', 'google', 'openrouter', or 'amazon-bedrock'."
            )
        self.top_k = top_k
        self.debug_retrieval = debug_retrieval
        
        # Initialize reranker (prefer semantic, fallback to lexical)
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.reranker = SentenceTransformerReranker(threshold=rerank_threshold)
                logger.info(f"Semantic reranker initialized with threshold={rerank_threshold:.2f}")
            except Exception as e:
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

        stop = threading.Event()  # set by a failed save: queued questions are skipped, not paid for

        def _process(idx: int, q: Dict) -> None:
            if stop.is_set():
                return
            q_id = q.get("id") or q.get("question_id") or str(idx)
            t0 = time.time()
            failure = None
            try:
                pair = self._generate_answer_pair(
                    library_name=library_name,
                    library_id=library_id,
                    question=q,
                    max_tokens=max_tokens_per_question
                )
            except Exception as e:
                failure = e
                pair = {
                    "question_id": q_id,
                    "question_text": q.get("question") or q.get("text", ""),
                    "library_name": library_name,
                    "category": q.get("category"),
                    "difficulty": q.get("difficulty"),
                    "persona": q.get("persona"),
                    "error": str(e),
                    "with_docs": None,
                    "without_docs": None
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

        answers = [results[i] for i in range(n)]
        ok_with = sum(1 for a in answers if a.get("with_docs"))
        ok_without = sum(1 for a in answers if a.get("without_docs"))
        errors = sum(1 for a in answers if a.get("error"))

        print("\n✓ Generated answers:", flush=True)
        print(f"  Context arm:  {ok_with}/{n}", flush=True)
        print(f"  Baseline:     {ok_without}/{n}", flush=True)
        if errors:
            print(f"  Errors:       {errors}", flush=True)
        return answers
    
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
        qid = question.get("id") or question.get("question_id") or "unknown_q"
        
        # Context arm
        with_docs_answer = None
        if self.mcp_client is not None:
            retrieved_docs, retrieval_metadata = self._retrieve_docs(
                library_id, question_text, max_tokens, return_metadata=True
            )
            
            if retrieved_docs:
                # Generate answer with retrieved context
                with_docs_answer = self._generate_with_docs(
                    question_text, retrieved_docs, retrieval_metadata
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
        
        # Baseline
        without_docs_answer = self._generate_without_docs(question_text)
        
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
        # Preserve ground truth chunk for chunk-grounded questions
        if question.get("ground_truth_chunk"):
            result["ground_truth_chunk"] = question["ground_truth_chunk"]
            result["question_source"] = question.get("question_source", "chunk")
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
            # Retrieve top-k docs
            # Note: Context7 client returns single doc, so we call it once for now
            # TODO: Update Context7 client to support top_k natively
            raw_docs = self.mcp_client.get_library_docs(
                library_id=library_id,
                query=question,
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
        retrieval_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Generate context-arm answer."""
        if not docs:
            # No LLM call is made — emit a zero-cost/zero-token metrics block so
            # this row carries the same telemetry shape as answered rows.
            empty = UsageRecord(model=self.model, provider=self.provider,
                                cost_usd=0.0, n_calls=0)
            token_usage = empty.as_token_usage_dict()
            token_usage["context_chars"] = 0
            return {
                "answer": "[No documentation retrieved]",
                "retrieved_docs": [],
                "model": self.model,
                "doc_source": "none",
                "retrieval_metadata": retrieval_metadata if self.debug_retrieval else None,
                "token_usage": token_usage,
                "metrics": empty.as_metrics_dict(answer_chars=0),
            }

        # ~4k tokens at ~4 chars/token; keeps prompt within most models' context window
        MAX_DOC_CHARS = 15_000

        # Format docs
        docs_text = "\n\n---\n\n".join(d["content"] for d in docs)
        context_chars = len(docs_text)
        docs_text_trimmed = docs_text[:MAX_DOC_CHARS]

        # Generate answer
        prompt = ANSWER_PROMPT_WITH_DOCS.format(
            question=question,
            docs=docs_text_trimmed,
        )

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

    def _generate_without_docs(self, question: str) -> Dict[str, Any]:
        """Generate baseline answer."""
        prompt = ANSWER_PROMPT_WITHOUT_DOCS.format(question=question)

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
        total_usage = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "with_docs": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "without_docs": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        for a in answers:
            for mode in ("with_docs", "without_docs"):
                u = (a.get(mode) or {}).get("token_usage") or {}
                for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    v = u.get(k, 0) or 0
                    total_usage[mode][k] += v
                    total_usage[k] += v

        return {
            "generated_at": self._get_timestamp(),
            "model": self.model,
            "provider": self.provider,
            "total_questions": len(answers),
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
        question_set_hash: str = None,
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
