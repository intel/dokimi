# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Validate and deduplicate generated questions."""

import os
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:  # optional: local, key-free embeddings for deduplication
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from dokimi.llm import ChatModel
from dokimi.llm import ChatOpenAI, ChatAnthropic, LANGCHAIN_AVAILABLE


#: Failures of the embedding *backend* — unreachable hub, uncached model,
#: rejected or exhausted API key — which skip deduplication. Anything else
#: (a TypeError from a malformed question, say) is a bug and still raises.
_BACKEND_ERRORS: Tuple[type[BaseException], ...] = (OSError, ConnectionError, TimeoutError)
try:
    import openai as _openai

    _openai_error = getattr(_openai, "OpenAIError", None)
    if isinstance(_openai_error, type) and issubclass(_openai_error, BaseException):
        _BACKEND_ERRORS = _BACKEND_ERRORS + (_openai_error,)
except ImportError:  # pragma: no cover - openai is optional
    pass

#: (default model, cosine threshold) per embedding backend. The threshold
#: belongs to the encoder, not to the pipeline: changing the backend without
#: changing the threshold silently changes what "duplicate" means.
#:
#: The local default is chosen against the error that matters for a golden set,
#: a *false merge*: two distinct questions on the same topic collapsed into one,
#: which deletes a question without telling anyone. Calibrated on an 81-question
#: perf set, with 40 LLM-written paraphrases as true duplicates and each
#: question's nearest *distinct* neighbour as the negative:
#:
#:   all-MiniLM-L6-v2 @ 0.83   recall 0.70, same-topic false merges 2.5%
#:   bge-small-en-v1.5 @ 0.85  recall 0.95, same-topic false merges 30%
#:   bge-small-en-v1.5 @ 0.94  recall 0.40, same-topic false merges 0%
#:
#: A missed duplicate costs one redundant question; a false merge costs one
#: question. Small sample (Wilson 95% on 28/40 recall: 0.55-0.82), one domain —
#: recalibrate before relying on it elsewhere. The OpenAI row keeps its
#: historical 0.85, which has not been measured against same-topic negatives.
EMBEDDING_BACKENDS: Dict[str, Tuple[str, float]] = {
    "openai": ("text-embedding-3-small", 0.85),
    # Same model `eval/reranker.py` loads, so the local cache is shared.
    "local": ("all-MiniLM-L6-v2", 0.83),
}


VALIDATION_PROMPT = """You are validating a technical question for documentation quality evaluation.

**Library:** {library_name}
**Question:** {question}

**Validation criteria:**
1. **Relevance (0-100):** Does the question mention {library_name} or its APIs/concepts?
2. **Answerability (0-100):** Is the question clear and answerable (not too vague)?
3. **Specificity (0-100):** Is it specific to {library_name} (not generic programming)?

Respond with ONLY a JSON object (no explanation):
{{
  "relevance": 85,
  "answerability": 90,
  "specificity": 80,
  "aggregate": 85,
  "reasoning": "Brief explanation"
}}

Aggregate score is the average of the three dimensions."""


class QuestionValidator:
    @staticmethod
    def _q_text(q: dict) -> str:
        return q.get("text") or q.get("question_text") or q.get("question") or ""

    """
    Validate questions for relevance/answerability and deduplicate.
    """
    
    def __init__(
        self,
        llm_model: str = "gpt-4o-mini",
        llm_provider: str = "openai",
        embedding_model: Optional[str] = None,
        threshold: int = 60,
        similarity_threshold: Optional[float] = None,
        api_key: Optional[str] = None,
        embedding_provider: str = "auto",
    ):
        """
        Args:
            llm_model: Model for validation (LLM-as-judge)
            llm_provider: "openai" or "anthropic"
            embedding_model: Model for embeddings (dedupe). Defaults to the
                chosen backend's model (see EMBEDDING_BACKENDS).
            threshold: Min aggregate score to keep (0-100)
            similarity_threshold: Cosine similarity for deduplication (0.0-1.0).
                Defaults to the chosen backend's measured threshold.
            api_key: Optional API key
            embedding_provider: "openai", "local" (sentence-transformers, no API
                key) or "auto" — OpenAI when a key is present, otherwise local.
        """
        self.llm_model = llm_model
        self.llm_provider = llm_provider
        self.api_key = api_key

        self.llm: Optional[ChatModel]
        if LANGCHAIN_AVAILABLE:
            if llm_provider == "openai":
                self.llm = ChatOpenAI(model=llm_model, api_key=api_key)
            elif llm_provider == "anthropic":
                self.llm = ChatAnthropic(model=llm_model, api_key=api_key)
            else:
                from dokimi.utils import get_llm
                self.llm = get_llm(llm_provider, llm_model, api_key or os.environ.get("OPENROUTER_API_KEY"))
        else:
            self.llm = None
        self.threshold = threshold

        # Pick the embedding backend before resolving its model/threshold, so a
        # fallback to the local encoder also moves the threshold with it.
        # `api_key` is the *LLM's* key; it is only an OpenAI key when the LLM is
        # OpenAI. An empty OPENAI_API_KEY= is no key at all.
        has_openai_key = bool(os.environ.get("OPENAI_API_KEY")) or bool(
            api_key and llm_provider == "openai"
        )
        requested = embedding_provider
        if embedding_provider == "auto":
            if OPENAI_AVAILABLE and has_openai_key:
                embedding_provider = "openai"
            elif SENTENCE_TRANSFORMERS_AVAILABLE:
                embedding_provider = "local"
            else:
                embedding_provider = "none"
        elif embedding_provider not in EMBEDDING_BACKENDS:
            raise ValueError(
                f"embedding_provider must be one of "
                f"{sorted(EMBEDDING_BACKENDS)} or 'auto', got {embedding_provider!r}"
            )
        default_model, default_threshold = EMBEDDING_BACKENDS.get(
            embedding_provider, (None, 0.85)
        )
        if embedding_model and embedding_provider == "local" and embedding_model.startswith(
            "text-embedding-"
        ):
            how = ("was resolved to 'local' (no OpenAI key)" if requested == "auto"
                   else "is 'local'")
            raise ValueError(
                f"embedding_model={embedding_model!r} is an OpenAI model, but the "
                f"embedding backend {how}. Use embedding_provider='openai' with a "
                f"key, or pass a sentence-transformers model."
            )
        if embedding_model and embedding_model != default_model and similarity_threshold is None:
            logger.warning(
                f"embedding_model={embedding_model!r} uses the {embedding_provider!r} "
                f"backend's threshold {default_threshold}, which was calibrated for "
                f"{default_model!r}; pass similarity_threshold for this model."
            )
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model or default_model
        self.similarity_threshold = (
            default_threshold if similarity_threshold is None else similarity_threshold
        )
        #: Whether the last `_deduplicate` call actually compared embeddings —
        #: not merely whether a backend was configured. A failed model load or
        #: embedding request leaves it False.
        self.dedupe_ran = False
        self.dedupe_error: Optional[str] = None

        self.openai_client: Optional[OpenAI] = None
        self._local_encoder: Optional[SentenceTransformer] = None
        if embedding_provider == "openai":
            if not OPENAI_AVAILABLE:
                raise ImportError(
                    "embedding_provider='openai' needs the openai package: "
                    "pip install openai"
                )
            # A non-OpenAI LLM key must not reach the OpenAI client; it then
            # reads OPENAI_API_KEY itself.
            self.openai_client = OpenAI(
                api_key=api_key if llm_provider == "openai" else None
            )
        elif embedding_provider == "local":
            if not SENTENCE_TRANSFORMERS_AVAILABLE:
                raise ImportError(
                    "embedding_provider='local' needs sentence-transformers: "
                    "pip install 'dokimi[embeddings]'"
                )
        else:
            logger.warning(
                "No embedding backend available — DEDUPLICATION IS DISABLED and "
                "near-duplicate questions will be kept. Set OPENAI_API_KEY, or "
                "install 'dokimi[embeddings]' for key-free local embeddings."
            )

        logger.info(
            f"QuestionValidator initialized: threshold={threshold}, "
            f"similarity={self.similarity_threshold}, "
            f"embeddings={embedding_provider}:{self.embedding_model}"
        )
    
    def validate_and_dedupe(
        self,
        library_name: str,
        questions: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Validate questions and remove duplicates.
        
        Args:
            library_name: Library name for validation context
            questions: List of question dicts from QuestionGenerator
        
        Returns:
            (validated_questions, stats)
            - validated_questions: Filtered and deduplicated list
            - stats: Dict with counts and removed questions
        """
        logger.info(f"Validating {len(questions)} questions...")
        
        # Step 0: Pre-filter synthetic "document analysis" questions
        SYNTHETIC_PATTERNS = [
            r"\bmentioned in\b",
            r"\baccording to the doc",
            r"\bdescribed in the (excerpt|doc)",
            r"\bstated in the doc",
            r"\bin the documentation excerpt\b",
            r"\bthe documentation (says|states|mentions|describes)\b",
            r"\bwhat (does|is|are) .{0,40} (mentioned|described|stated|listed) in",
        ]
        import re
        pre_filtered = []
        pre_rejected = []
        for q in questions:
            text = self._q_text(q).lower()
            rejected = any(re.search(p, text, re.IGNORECASE) for p in SYNTHETIC_PATTERNS)
            if rejected:
                pre_rejected.append(q)
                logger.info(f"Pre-filter rejected (synthetic framing): {self._q_text(q)[:80]}")
            else:
                pre_filtered.append(q)
        if pre_rejected:
            logger.warning(f"Pre-filter removed {len(pre_rejected)} synthetic questions")
        
        # Step 1: Validate each question (LLM scoring)
        validated = []
        n_unscored = 0
        for q in pre_filtered:
            score = self._validate_question(library_name, self._q_text(q))
            if score is not None:
                q["validation_score"] = score["aggregate"]
                q["validation_details"] = score
                n_unscored += bool(score.get("unscored"))
                if score["aggregate"] >= self.threshold:
                    validated.append(q)

        logger.info(f"After validation: {len(validated)}/{len(questions)} passed (threshold={self.threshold})")
        if n_unscored:
            # Pass-through keeps the pipeline running, but a run that scored
            # nothing must not look like a run that scored everything.
            logger.warning(
                f"{n_unscored}/{len(pre_filtered)} questions were NOT actually "
                f"scored (no LLM available or the call failed) and were passed "
                f"through at 100 — this run did not validate them"
            )
        
        # Step 2: Deduplicate (embedding similarity)
        deduplicated, dup_groups = self._deduplicate(validated)
        
        logger.info(f"After deduplication: {len(deduplicated)}/{len(validated)} unique")
        
        stats = {
            "initial_count": len(questions),
            "after_prefilter": len(pre_filtered),
            "removed_synthetic": len(pre_rejected),
            "after_validation": len(validated),
            "after_deduplication": len(deduplicated),
            "removed_low_score": len(pre_filtered) - len(validated),
            "removed_duplicates": len(validated) - len(deduplicated),
            "duplicate_groups": dup_groups,
            # What the run actually did, so a no-op is visible in the artifact
            # and not only in the log.
            "unscored_questions": n_unscored,
            "validation_performed": n_unscored < len(pre_filtered),
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "similarity_threshold": self.similarity_threshold,
            "deduplication_performed": self.dedupe_ran,
            "deduplication_error": self.dedupe_error,
        }
        
        return deduplicated, stats
    
    def _validate_question(self, library_name: str, question_text: str) -> Optional[Dict[str, Any]]:
        """Validate a single question using LLM."""

        
        try:
            prompt = VALIDATION_PROMPT.format(
                library_name=library_name,
                question=question_text
            )
            
            if self.llm is None:
                return {"relevance": 100, "answerability": 100, "specificity": 100,
                        "aggregate": 100, "unscored": True,
                        "reasoning": "LLM unavailable; pass-through"}

            response = self.llm.invoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
            
            from dokimi.llm import extract_json_object
            score = extract_json_object(raw)
            
            # Validate structure
            required = {"relevance", "answerability", "specificity", "aggregate"}
            if not required.issubset(score.keys()):
                raise ValueError(f"Missing keys in validation response: {required - score.keys()}")
            
            return score
            
        except Exception as e:
            logger.error(f"Validation failed for question: {question_text[:50]}... — {e}")
            return {"relevance": 100, "answerability": 100, "specificity": 100,
                    "aggregate": 100, "unscored": True,
                    "reasoning": f"LLM validation error; pass-through: {e}"}
    
    def _deduplicate(
        self,
        questions: List[Dict[str, Any]],
        protect: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[List[str]]]:
        """
        Deduplicate questions using embedding similarity.

        Args:
            protect: Questions for which this returns True are never dropped.
                In a duplicate group containing any, every protected member is
                kept and only the unprotected ones go; otherwise the longest
                text is kept, as before. Used for curated questions, which a
                generated paraphrase must not replace.

        A backend that fails at load or request time (no network, uncached
        model, rejected key) skips deduplication with a warning instead of
        aborting the run — the same outcome as having no backend, which is what
        this step did before it had a local one. `dedupe_ran` records which.

        Returns:
            (unique_questions, duplicate_groups)
        """
        self.dedupe_ran = False
        self.dedupe_error = None
        if not self._embeddings_available():
            logger.warning(
                "Deduplication skipped (no embedding backend) — "
                f"{len(questions)} questions kept as-is, duplicates included"
            )
            return questions, []

        if len(questions) == 0:
            self.dedupe_ran = True
            return [], []

        texts = [self._q_text(q) for q in questions]
        try:
            embeddings = self._get_embeddings(texts)
        except _BACKEND_ERRORS as exc:
            self.dedupe_error = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning(
                f"Deduplication skipped — the {self.embedding_provider} embedding "
                f"backend failed ({self.dedupe_error}); {len(questions)} questions "
                f"kept as-is, duplicates included"
            )
            return questions, []
        self.dedupe_ran = True
        
        # Build similarity matrix and find duplicates
        try:
            import numpy as np

            embeddings_array = np.array(embeddings)
            # Normalize
            norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
            embeddings_norm = embeddings_array / norms

            # Cosine similarity matrix
            similarity = embeddings_norm @ embeddings_norm.T
            sim_get = lambda i, j: float(similarity[i, j])
        except ImportError:
            logger.warning("numpy not available - using pure-python cosine similarity")

            def _cos(a, b):
                dot = sum(x * y for x, y in zip(a, b, strict=False))
                na = sum(x * x for x in a) ** 0.5
                nb = sum(y * y for y in b) ** 0.5
                if na == 0 or nb == 0:
                    return 0.0
                return dot / (na * nb)

            similarity = [[_cos(embeddings[i], embeddings[j]) for j in range(len(questions))] for i in range(len(questions))]
            sim_get = lambda i, j: similarity[i][j]
        
        # Find duplicate groups (similarity > threshold)
        unique_indices = []
        duplicate_groups = []
        persona_merge_map = {}  # best_idx -> list of all similar indices
        seen = set()
        
        for i in range(len(questions)):
            if i in seen:
                continue
            
            # Find all questions similar to i (including i itself)
            similar = [j for j in range(i, len(questions))
                      if sim_get(i, j) > self.similarity_threshold and j not in seen]
            
            if len(similar) > 1:
                # Duplicate group found
                duplicate_groups.append([self._q_text(questions[j]) for j in similar])
                protected = [j for j in similar if protect and protect(questions[j])]
                if protected:
                    # Keep every protected member; personas go to the first.
                    unique_indices.extend(protected)
                    persona_merge_map[protected[0]] = similar
                else:
                    best_idx = max(similar, key=lambda j: len(self._q_text(questions[j])))
                    unique_indices.append(best_idx)
                    persona_merge_map[best_idx] = similar  # Save for merging later
                seen.update(similar)
            else:
                unique_indices.append(i)
                seen.add(i)
        
        # Build unique questions list
        unique_questions = [questions[i] for i in sorted(unique_indices)]
        
        # Merge persona lists for kept questions from duplicate groups. The kept
        # question is the same object in `unique_questions`, so update it
        # directly: matching it back by `id` sent the personas to the first
        # id-less question in the list (None == None).
        for kept_idx, similar_indices in persona_merge_map.items():
            kept_q = questions[kept_idx]
            all_personas = set(kept_q.get("personas", []))
            for j in similar_indices:
                all_personas.update(questions[j].get("personas", []))
            kept_q["personas"] = list(all_personas)
        
        return unique_questions, duplicate_groups
    
    def _embeddings_available(self) -> bool:
        return self.openai_client is not None or self.embedding_provider == "local"

    def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Embed questions with the configured backend."""
        if self.embedding_provider == "local":
            return self._get_local_embeddings(texts)
        try:
            # Callers check _embeddings_available(); the "openai" backend sets both.
            assert self.openai_client is not None and self.embedding_model is not None
            response = self.openai_client.embeddings.create(
                input=texts,
                model=self.embedding_model
            )
            return [item.embedding for item in response.data]
        except Exception as e:
            logger.error(f"Embedding request failed: {e}")
            raise

    def _get_local_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Embed with a local sentence-transformers model — no API key. Offline
        use needs the model cached *and* ``HF_HUB_OFFLINE=1``: without it the
        hub client still tries the network first, and behind a dead proxy that
        is minutes of retries before it falls back to the cache."""
        if self._local_encoder is None:
            logger.info(f"Loading local embedding model {self.embedding_model}")
            self._local_encoder = SentenceTransformer(self.embedding_model)
        vectors = self._local_encoder.encode(
            list(texts), normalize_embeddings=False, show_progress_bar=False
        )
        return [[float(x) for x in row] for row in vectors]
