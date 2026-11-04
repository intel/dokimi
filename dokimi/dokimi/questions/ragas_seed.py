# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Extract seed topics from library docs using RAGAS knowledge graph approach."""

import logging
import hashlib
import json
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class RagasSeedExtractor:
    """
    Extract seed topics from library documentation via Context7.

    Uses RAGAS-inspired approach:
    1. Fetch docs from Context7
    2. Chunk into manageable segments
    3. Extract key entities/concepts via LLM
    4. Return deduplicated topic list
    """

    def __init__(
        self,
        mcp_client=None,
        llm=None,
        cache_dir: Optional[Path] = None,
    ):
        """
        Args:
            mcp_client: MCP client for fetching docs (e.g. Context7Client)
            llm:        LangChain-compatible LLM for topic extraction
            cache_dir:  Optional directory to cache extracted topics
        """
        self.mcp_client = mcp_client
        self.llm = llm
        self.cache_dir = cache_dir
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_topics(
        self,
        library_id: str,
        library_name: str,
        max_topics: int = 20,
        max_tokens: int = 8000,
    ) -> List[str]:
        """
        Extract seed topics for question generation.

        Args:
            library_id:   Context7 library ID (e.g. "uxlfoundation/oneTBB")
            library_name: Human-readable name (e.g. "oneTBB")
            max_topics:   Maximum number of topics to return
            max_tokens:   Max tokens to fetch from Context7 per query

        Returns:
            List of topic strings, e.g.:
            ["parallel_for", "task_arena", "flow_graph", "tbb::blocked_range", ...]
        """
        cache_key = hashlib.sha256(
            f"{library_id}:{max_topics}:{max_tokens}".encode()
        ).hexdigest()

        # Cache hit?
        if self.cache_dir:
            cache_file = self.cache_dir / f"topics_{cache_key}.json"
            if cache_file.exists():
                logger.info("Topics cache hit for %s", library_id)
                return json.loads(cache_file.read_text(encoding="utf-8"))

        # 1. Fetch raw docs
        docs = self._fetch_docs(library_id, library_name, max_tokens)
        if not docs:
            logger.warning("No docs fetched for %s", library_id)
            return self._fallback_topics(library_name)

        # 2. Extract topics via LLM (or simple heuristics if no LLM)
        topics = self._extract_via_llm(docs, library_name, max_topics)

        # 3. Cache
        if self.cache_dir:
            cache_file.write_text(json.dumps(topics), encoding="utf-8")
            logger.info("Cached %d topics to %s", len(topics), cache_file)

        return topics

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_docs(
        self, library_id: str, library_name: str, max_tokens: int
    ) -> str:
        """Fetch documentation text from MCP client."""
        if self.mcp_client is None:
            logger.warning("No MCP client configured, using empty docs")
            return ""

        try:
            # Broad overview query to get general structure
            chunks = self.mcp_client.get_library_docs(
                library_id,
                query=f"{library_name} API overview concepts",
                max_tokens=max_tokens,
            )
            return "\n\n".join(c["content"] for c in chunks)
        except Exception as e:
            logger.error("Failed to fetch docs for %s: %s", library_id, e)
            return ""

    def _extract_via_llm(
        self, docs: str, library_name: str, max_topics: int
    ) -> List[str]:
        """Use LLM to extract key topics/concepts from docs."""
        if self.llm is None:
            logger.info("No LLM configured, falling back to heuristic extraction")
            return self._extract_heuristic(docs, library_name, max_topics)

        prompt = (
            f"You are analyzing documentation for {library_name}.\n\n"
            f"Documentation excerpt:\n{docs[:6000]}\n\n"
            f"Extract up to {max_topics} distinct technical topics, API names, "
            f"concepts, and features that users would ask questions about.\n"
            f"Return ONLY a JSON array of short strings (no explanation):\n"
            f'["topic1", "topic2", ...]'
        )

        try:
            response = self.llm.invoke(prompt)
            raw = response.content if hasattr(response, "content") else str(response)
            from dokimi.llm import extract_json_array
            topics = extract_json_array(raw)
            # Clean and deduplicate
            topics = list(dict.fromkeys(str(t).strip() for t in topics if t))
            return topics[:max_topics]
        except Exception as e:
            logger.error("LLM topic extraction failed: %s — falling back to heuristic", e)
            return self._extract_heuristic(docs, library_name, max_topics)

    @staticmethod
    def _extract_heuristic(docs: str, library_name: str, max_topics: int) -> List[str]:
        """
        Heuristic topic extraction (no LLM required).

        Looks for:
        - Code identifiers with :: (C++ namespaced names)
        - Backtick-quoted terms
        - CamelCase identifiers
        """
        import re

        topics: List[str] = []

        # C++ namespaced identifiers: tbb::parallel_for
        ns_pattern = re.compile(r'\b[a-z]+::[a-zA-Z_]+\b')
        topics.extend(ns_pattern.findall(docs))

        # Backtick-quoted terms (Markdown code spans)
        bt_pattern = re.compile(r'`([^`]{3,40})`')
        topics.extend(bt_pattern.findall(docs))

        # Unique, preserve order, limit
        seen: Dict[str, bool] = {}
        result = []
        for t in topics:
            t = t.strip()
            if t and t not in seen:
                seen[t] = True
                result.append(t)
            if len(result) >= max_topics:
                break

        return result or RagasSeedExtractor._fallback_topics(library_name)

    @staticmethod
    def _fallback_topics(library_name: str) -> List[str]:
        """Return generic fallback topics when extraction fails.

        Product-specific topic lists are evaluation content: supply them with
        ``eval questions generate --topics <file>`` instead of hard-coding them
        here, so the fallback is the same for every library.
        """
        return ["installation", "getting started", "API overview",
                "performance tuning", "troubleshooting", "examples"]
