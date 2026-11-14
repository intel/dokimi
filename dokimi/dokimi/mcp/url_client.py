# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""URL-based documentation source client."""

import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import httpx

from . import MCPClient, MCPConnectionError
from .utils import read_cache_text, strip_html, score_chunk, split_paragraphs, write_cache_text

logger = logging.getLogger(__name__)


class URLClient(MCPClient):
    """
    Documentation client that fetches content from an arbitrary URL.

    Fetches once, caches the result (if cache_dir is set), then splits
    the text into paragraphs and returns the most query-relevant ones.

    Usage::

        client = URLClient("https://spec.example.com/api.html")
        docs = client.get_library_docs("https://...", "parallel_for")
    """

    def __init__(
        self,
        url: str,
        timeout: int = 30,
        cache_dir: Optional[Path] = None,
        max_page_size_kchars: int = 1024,
    ):
        """
        Args:
            url: Documentation URL to fetch.
            timeout: HTTP request timeout in seconds.
            cache_dir: Directory for caching fetched pages (None = no cache).
            max_page_size_kchars: Truncate pages larger than this (measured in characters).
        """
        self.url = url
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_page_size_chars = max_page_size_kchars * 1024

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── MCPClient interface ────────────────────────────────────────────────

    def resolve_library_id(self, library_name: str) -> str:
        """Return the URL as the canonical library ID."""
        return self.url

    def get_library_docs(
        self,
        library_id: str,
        query: str,
        max_results: int = 5,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the URL, split into paragraphs, return top query-relevant chunks.

        Args:
            library_id: Ignored (URL is set at construction time).
            query: Natural-language search query.
            max_results: Maximum number of paragraph chunks to return.
            max_tokens: Soft limit — output capped at max_tokens*4 total chars.

        Returns:
            List of dicts with keys: content, source, url, library_id, query.
        """
        raw = self._fetch()
        if not raw:
            return []

        paragraphs = split_paragraphs(raw)
        if not paragraphs:
            # Fall back to the whole page as one chunk
            chunk_limit = max_tokens * 4
            return [{
                "content": raw[:chunk_limit],
                "source": "url",
                "url": self.url,
                "library_id": library_id,
                "query": query,
                "relevance_score": score_chunk(query, raw[:chunk_limit]),
            }]

        scored = sorted(
            ((p, score_chunk(query, p)) for p in paragraphs),
            key=lambda x: -x[1],
        )

        # Combine top paragraphs up to max_tokens*4 chars total
        char_limit = max_tokens * 4
        results = []
        total_chars = 0
        for para, score in scored[:max_results]:
            if total_chars >= char_limit:
                break
            results.append({
                "content": para[: char_limit - total_chars],
                "source": "url",
                "url": self.url,
                "library_id": library_id,
                "query": query,
                "relevance_score": round(score, 4),
            })
            total_chars += len(para)

        logger.info(
            f"URLClient: returning {len(results)} chunks for query='{query[:40]}...'"
        )
        return results

    def check_connection(self) -> bool:
        """Return True if the URL is reachable (HTTP 2xx)."""
        try:
            resp = httpx.head(self.url, timeout=self.timeout, follow_redirects=True)
            return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.debug(f"URLClient.check_connection failed: {exc}")
            return False

    # ── Helpers ───────────────────────────────────────────────────────────

    def _cache_key(self) -> str:
        return hashlib.sha256(self.url.encode()).hexdigest()

    def _fetch(self) -> str:
        """Fetch URL (with optional caching). Returns cleaned plain text."""
        # Try cache first
        if self.cache_dir:
            # The size limit is in the name: a page cut at one limit is not whole at another.
            cache_file = self.cache_dir / f"{self._cache_key()}-{self.max_page_size_chars}.txt"
            cached = read_cache_text(cache_file)
            if cached is not None and cached.strip():
                logger.info(f"URLClient: cache hit for {self.url}")
                return cached
            if cached is not None:
                logger.warning(f"URLClient: ignoring empty cache entry {cache_file}; fetching again")

        logger.info(f"URLClient: fetching {self.url}")
        try:
            resp = httpx.get(
                self.url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": "dokimi/1.0"},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MCPConnectionError(f"URLClient HTTP error {exc.response.status_code}: {self.url}") from exc
        except httpx.TimeoutException as exc:
            raise MCPConnectionError(f"URLClient timeout fetching {self.url}") from exc
        except Exception as exc:
            raise MCPConnectionError(f"URLClient fetch failed: {exc}") from exc

        content_type = resp.headers.get("content-type", "")
        text = resp.text[: self.max_page_size_chars]

        if "html" in content_type or text.lstrip().startswith("<"):
            text = strip_html(text)

        # Persist to cache; a page with no text is not worth replaying
        if self.cache_dir and text.strip() and write_cache_text(cache_file, text):
            logger.info(f"URLClient: cached {self.url} → {cache_file}")

        return text
