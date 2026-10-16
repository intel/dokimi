# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Context7 MCP client implementation."""

import urllib.parse
from typing import List, Dict, Any, Optional
import httpx
from pathlib import Path
import hashlib
import logging

from . import MCPClient, MCPConnectionError, MCPLibraryNotFoundError

logger = logging.getLogger(__name__)


class Context7Client(MCPClient):
    """
    Context7 documentation retrieval client.
    
    Context7 provides curated documentation for popular libraries
    via a simple HTTP API optimized for LLM consumption.
    """

    # Context7 ids are minted by Context7, not derived from a product name.
    requires_declared_library_id = True


    def __init__(
        self, 
        endpoint: str = "https://context7.com",
        cache_dir: Optional[Path] = None,
        timeout: int = 30,
        max_retries: int = 3
    ):
        """
        Initialize Context7 client.
        
        Args:
            endpoint: Base URL for Context7 API
            cache_dir: Directory for caching responses (None = no cache)
            timeout: Request timeout in seconds
            max_retries: Number of retry attempts
        """
        self.endpoint = endpoint.rstrip('/')
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.max_retries = max_retries
        
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def resolve_library_id(self, library_name: str) -> str:
        """
        Pass an already-qualified Context7 library ID through, or fail loudly.

        A Context7 ID is either ``<org>/<repo>`` for a GitHub-backed library
        or ``<host>_<path>`` for a documentation website (for example
        ``intelpython_github_io_dpnp``). Neither can be derived from a bare
        product name, so an unqualified name is an error rather than a guess:
        a guessed ID is well-formed but usually does not exist, and the run
        then completes with empty retrieval attributed to the doc source.

        The website form is indistinguishable from a registry key that happens
        to contain an underscore (``mkl_extensions``), so a *registered*
        product is never routed here: ``requires_declared_library_id`` makes
        :func:`dokimi.mcp.factory.resolve_library_id_for_product`
        raise for a registered product with no ``context7_id``. What reaches
        this method is a name the caller typed, and an underscored one is
        taken at its word as a website ID.

        Args:
            library_name: Qualified Context7 library ID.

        Returns:
            The same ID, unchanged.

        Raises:
            ValueError: If *library_name* is not a qualified Context7 ID.
        """
        name = library_name.strip()

        # GitHub repo form (org/repo) or website form (no slash, underscores).
        if "/" in name or "_" in name:
            return name

        raise ValueError(
            f"'{library_name}' is not a Context7 library ID. Context7 IDs look "
            "like 'uxlfoundation/oneTBB' (GitHub repo) or "
            "'intelpython_github_io_dpnp' (docs website), and cannot be derived "
            "from a product name. Either declare 'context7_id:' for the product "
            "in products.yaml, or pass --context7-id on the command line."
        )

    def get_library_docs(
        self, 
        library_id: str, 
        query: str,
        max_results: int = 5,
        max_tokens: int = 8000
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant documentation chunks for a query.
        
        Args:
            library_id: Library identifier (e.g., "uxlfoundation/oneTBB")
            query: Search query (question or topic)
            max_results: Maximum number of chunks (Context7 returns merged text)
            max_tokens: Maximum tokens to retrieve
            
        Returns:
            List with single merged doc chunk:
            [{"content": str, "source": str, "library_id": str, "query": str}]
        """
        # Check cache first
        if self.cache_dir:
            cache_key = hashlib.sha256(
                f"{library_id}:{query}:{max_tokens}".encode()
            ).hexdigest()
            cache_file = self.cache_dir / f"{cache_key}.txt"
            
            if cache_file.exists():
                logger.info(f"Cache hit for {library_id} query: {query[:50]}...")
                content = cache_file.read_text(encoding="utf-8")
                return [{
                    "content": content,
                    "source": "context7_cache",
                    "library_id": library_id,
                    "query": query,
                    "cached": True
                }]
        
        # Fetch from Context7
        # Support both GitHub repos (uxlfoundation/oneTBB) and websites (intelpython_github_io_dpnp)
        # Website IDs: no slash + (underscore OR 'github_io' pattern)
        # This avoids misclassifying GitHub repos with underscores (e.g., some_org/repo)
        is_website = "/" not in library_id and ("_" in library_id or "github_io" in library_id)

        if is_website:
            # Website format: websites/intelpython_github_io_dpnp
            url = (
                f"{self.endpoint}/websites/{library_id}/llms.txt"
                f"?tokens={max_tokens}"
                f"&topic={urllib.parse.quote(query)}"
            )
        else:
            # GitHub repo format: uxlfoundation/oneTBB (or some_org/repo)
            url = (
                f"{self.endpoint}/{library_id}/llms.txt"
                f"?tokens={max_tokens}"
                f"&topic={urllib.parse.quote(query)}"
            )
        
        logger.info(f"Fetching from Context7: {library_id} query={query[:50]}...")
        
        try:
            response = httpx.get(url, timeout=self.timeout, follow_redirects=True)
            response.raise_for_status()
            content = response.text
            
            # Context7 sometimes returns HTML or error text with HTTP 200
            # Basic validation: check if response looks like documentation
            if content.startswith(("<!DOCTYPE", "<html")):
                raise MCPLibraryNotFoundError(
                    f"Context7 returned HTML (library not found?): {library_id}"
                )
            
            if len(content) < 100:
                logger.warning(f"Context7 returned suspiciously short response: {len(content)} bytes")
            
            # Cache successful response
            if self.cache_dir:
                cache_file.write_text(content, encoding="utf-8")
                logger.info(f"Cached response to {cache_file}")
            
            return [{
                "content": content,
                "source": "context7",
                "library_id": library_id,
                "query": query,
                "cached": False,
                "url": url
            }]
            
        except (MCPLibraryNotFoundError, MCPConnectionError):
            raise  # re-raise our own exceptions as-is
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise MCPLibraryNotFoundError(
                    f"Library not found in Context7: {library_id}"
                ) from e
            raise MCPConnectionError(f"Context7 HTTP error: {e}") from e

        except httpx.TimeoutException as e:
            raise MCPConnectionError(f"Context7 request timeout after {self.timeout}s") from e

        except Exception as e:
            raise MCPConnectionError(f"Context7 request failed: {e}") from e
    
    # A remote source has nothing local to probe, so the caller must name the library
    # (MCPClient.check_connection documents this exception to its signature).
    def check_connection(self, library_id: str, query: str = "overview") -> bool:  # type: ignore[override]
        """
        Verify that Context7 is accessible, by probing a caller-named library.

        Unlike the other doc-source clients this one has nothing local to
        check, so the probe target is a parameter: the caller names a library
        ID it expects to exist (for example the product's ``context7_id``).

        Args:
            library_id: Qualified Context7 library ID to probe.
            query: Topic to ask for; any term the library documents will do.

        Returns:
            True if connection is successful
        """
        try:
            docs = self.get_library_docs(
                library_id,
                query,
                max_tokens=500
            )
            return len(docs) > 0 and len(docs[0]["content"]) > 0
        except Exception as e:
            logger.error(f"Context7 connection check failed: {e}")
            return False


def create_context7_client(cache_dir: Optional[Path] = None) -> Context7Client:
    """
    Factory function to create a Context7 client with default settings.
    
    Args:
        cache_dir: Cache directory (defaults to .cache/context7)
    
    Returns:
        Configured Context7Client instance
    """
    if cache_dir is None:
        cache_dir = Path(".cache") / "context7"
    
    return Context7Client(cache_dir=cache_dir)
