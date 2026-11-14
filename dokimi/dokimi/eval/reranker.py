# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Rerank retrieved documentation chunks by relevance to question."""

import re
import logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer, util
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except Exception:  # noqa: BLE001 - importing torch can raise more than ImportError
    SENTENCE_TRANSFORMERS_AVAILABLE = False


class SimpleReranker:
    """
    Rerank docs using lexical overlap + heuristics.
    
    Scoring components:
    - Token overlap (60%): shared keywords between question and doc
    - Length score (20%): prefer longer, more substantial chunks
    - Code bonus (20%): if both mention code-like tokens
    """
    
    def __init__(self, threshold: float = 0.3):
        """
        Args:
            threshold: Minimum score (0-1) to keep a doc. Docs below are filtered.
        """
        self.threshold = threshold
    
    def score_relevance(self, question: str, doc_content: str) -> float:
        """
        Score document relevance to question (0-1).
        
        Args:
            question: User question text
            doc_content: Documentation content to score
        
        Returns:
            Relevance score 0-1 (higher = more relevant)
        """
        q_tokens = set(self._tokenize(question))
        d_tokens = set(self._tokenize(doc_content))
        
        if not q_tokens:
            return 0.0
        
        # 1. Token overlap (Jaccard-like)
        overlap = len(q_tokens & d_tokens) / len(q_tokens)
        
        # 2. Length score (prefer 300-1000 chars, penalize too short)
        length = len(doc_content)
        if length < 100:
            length_score = 0.1
        elif length < 300:
            length_score = length / 300 * 0.5
        elif length < 1000:
            length_score = 0.5 + (length - 300) / 700 * 0.5
        else:
            length_score = 1.0
        
        # 3. Code presence bonus
        code_pattern = r'\b(function|class|template|namespace|tbb::|std::|parallel_|flow_)\b'
        q_has_code = bool(re.search(code_pattern, question, re.IGNORECASE))
        d_has_code = bool(re.search(code_pattern, doc_content, re.IGNORECASE))
        code_bonus = 0.2 if (q_has_code and d_has_code) else 0.0
        
        # Weighted sum
        score = (overlap * 0.6) + (length_score * 0.2) + code_bonus
        
        return min(score, 1.0)
    
    def rerank(
        self,
        question: str,
        docs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Rerank documents by relevance, filter below threshold.
        
        Args:
            question: User question
            docs: List of doc dicts with 'content' field
        
        Returns:
            Filtered and sorted docs (highest relevance first).
            Each doc gets 'relevance_score' field added.
        """
        if not docs:
            return []
        
        scored = []
        for doc in docs:
            content = doc.get('content', '')
            score = self.score_relevance(question, content)
            
            if score >= self.threshold:
                doc_copy = doc.copy()
                doc_copy['relevance_score'] = round(score, 3)
                scored.append((score, doc_copy))
        
        # Sort by score descending
        scored.sort(reverse=True, key=lambda x: x[0])
        
        result = [doc for _, doc in scored]
        
        logger.info(
            f"Reranked {len(docs)} docs → kept {len(result)} "
            f"(threshold={self.threshold:.2f})"
        )
        
        return result
    
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Extract alphanumeric tokens (3+ chars) from text."""
        return re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b', text.lower())


class SentenceTransformerReranker:
    """
    Semantic reranker using sentence-transformers cosine similarity.

    Much more robust than lexical overlap when docs share generic keywords
    (e.g. "tbb::", "parallel") but are semantically off-topic.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", threshold: float = 0.35):
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError("sentence-transformers not installed")

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.threshold = threshold
        logger.info(f"Semantic reranker loaded: {model_name}, threshold={threshold:.2f}")

    def rerank(self, question: str, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rerank docs by semantic similarity to question."""
        if not docs:
            return []

        doc_texts = [d.get("content", "") for d in docs]
        q_emb = self.model.encode(question, convert_to_tensor=True)
        d_embs = self.model.encode(doc_texts, convert_to_tensor=True)
        scores = util.cos_sim(q_emb, d_embs)[0].cpu().tolist()

        ranked = []
        for score, doc in zip(scores, docs, strict=False):
            s = float(score)
            if s >= self.threshold:
                doc_copy = doc.copy()
                doc_copy["relevance_score"] = round(s, 3)
                ranked.append((s, doc_copy))

        ranked.sort(reverse=True, key=lambda x: x[0])
        result = [doc for _, doc in ranked]
        logger.info(
            f"Semantic reranked {len(docs)} docs → kept {len(result)} "
            f"(threshold={self.threshold:.2f}, model={self.model_name})"
        )
        return result
