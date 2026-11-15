# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Local Markdown/HTML documentation source client."""

import logging
from pathlib import Path
from typing import List, Dict, Any

from . import MCPClient, MCPConnectionError
from .utils import strip_html, score_chunk

logger = logging.getLogger(__name__)

# Supported doc file extensions
_DOC_EXTENSIONS = {".md", ".rst", ".html", ".htm", ".txt"}


class LocalMarkdownClient(MCPClient):
    """
    Documentation client that reads from a local directory.

    Supports Markdown (.md), reStructuredText (.rst), plain text (.txt),
    and Sphinx HTML (.html / .htm) files.

    Usage::

        client = LocalMarkdownClient(Path("/path/to/docs"))
        docs = client.get_library_docs("local:/path/to/docs", "parallel_for")
    """

    def __init__(
        self,
        path: Path,
        encoding: str = "utf-8",
        max_file_size_kb: int = 512,
    ):
        """
        Args:
            path: Root directory (or single file) containing documentation.
            encoding: Text encoding to use when reading files.
            max_file_size_kb: Skip files larger than this (avoids OOM on huge HTML).
        """
        self.path = Path(path)
        self.encoding = encoding
        self.max_file_size_bytes = max_file_size_kb * 1024

    # ── MCPClient interface ────────────────────────────────────────────────

    def resolve_library_id(self, library_name: str) -> str:
        """Return a canonical ID for this local source."""
        return f"local:{self.path}"

    def get_library_docs(
        self,
        library_id: str,
        query: str,
        max_results: int = 5,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """
        Return the most query-relevant documentation chunks from the local dir.

        Each chunk corresponds to one file (truncated to ~max_tokens/5 chars).
        Files are scored by simple keyword overlap and the top *max_results*
        are returned.

        Args:
            library_id: Ignored (path is set at construction time).
            query: Natural-language search query.
            max_results: Maximum number of file chunks to return.
            max_tokens: Soft limit — each chunk is capped at max_tokens*4 chars.

        Returns:
            List of dicts with keys: content, source, file, library_id, query.
        """
        if not self.path.exists():
            raise MCPConnectionError(f"LocalMarkdownClient: path does not exist: {self.path}")

        files = self._collect_files()
        if not files:
            logger.warning(f"LocalMarkdownClient: no doc files found under {self.path}")
            return []

        char_limit = max_tokens * 4  # rough chars-per-token estimate

        scored: List[tuple] = []
        for fp in files:
            try:
                content = self._read_file(fp, char_limit)
            except Exception as exc:  # noqa: BLE001 - logged at debug; one unreadable file is skipped
                logger.debug(f"Skipping {fp}: {exc}")
                continue

            if not content:
                continue

            score = score_chunk(query, content)
            scored.append((score, fp, content))

        # Sort by score descending, then by file name for determinism
        scored.sort(key=lambda x: (-x[0], str(x[1])))

        results = []
        for score, fp, content in scored[:max_results]:
            results.append({
                "content": content,
                "source": "local",
                "file": str(fp.relative_to(self.path) if fp.is_relative_to(self.path) else fp),
                "library_id": library_id,
                "query": query,
                "relevance_score": round(score, 4),
            })

        logger.info(
            f"LocalMarkdownClient: returning {len(results)}/{len(scored)} chunks "
            f"for query='{query[:40]}...'"
        )
        return results

    def check_connection(self) -> bool:
        """Return True if the path exists and contains at least one doc file."""
        if not self.path.exists():
            return False
        return len(self._collect_files()) > 0

    # ── Helpers ───────────────────────────────────────────────────────────

    def _collect_files(self) -> List[Path]:
        """Recursively collect supported doc files under self.path."""
        if self.path.is_file():
            return [self.path] if self.path.suffix.lower() in _DOC_EXTENSIONS else []

        files = []
        for fp in self.path.rglob("*"):
            if fp.is_file() and fp.suffix.lower() in _DOC_EXTENSIONS:
                if fp.stat().st_size <= self.max_file_size_bytes:
                    files.append(fp)
        return files

    def _read_file(self, fp: Path, char_limit: int) -> str:
        """Read a file and return cleaned text, truncated to char_limit."""
        raw = fp.read_text(encoding=self.encoding, errors="replace")
        if fp.suffix.lower() in {".html", ".htm"}:
            raw = strip_html(raw)
        return raw[:char_limit]
