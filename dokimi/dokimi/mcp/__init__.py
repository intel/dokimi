# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""MCP (Model Context Protocol) base interface and doc-source clients."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPConnectionError",
    "MCPLibraryNotFoundError",
]


class MCPClient(ABC):
    """Abstract base class for MCP clients."""

    #: True when this source's library ids cannot be derived from a product
    #: name, so a registered product has to declare one. Local sources (files,
    #: OKF bundles) leave it False because they ignore the name and return
    #: their own canonical id; :class:`~dokimi.mcp.context7.
    #: Context7Client` sets it True, and
    #: :func:`dokimi.mcp.factory.resolve_library_id_for_product`
    #: refuses to hand such a client a bare registry key.
    requires_declared_library_id: bool = False

    @abstractmethod
    def resolve_library_id(self, library_name: str) -> str:
        """
        Resolve library name to library ID.
        
        Args:
            library_name: Human-readable library name (e.g., "oneTBB")
            
        Returns:
            Library ID (e.g., "uxlfoundation/oneTBB")
        """
    
    @abstractmethod
    def get_library_docs(
        self, 
        library_id: str, 
        query: str,
        max_results: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant documentation chunks for a query.
        
        Args:
            library_id: Library identifier
            query: Search query (question or topic)
            max_results: Maximum number of doc chunks to return
            
        Returns:
            List of doc chunks with metadata:
            [
                {
                    "content": str,
                    "source": str,
                    "relevance_score": float,
                    "metadata": dict
                }
            ]
        """
    
    @abstractmethod
    def check_connection(self) -> bool:
        """
        Verify that the MCP server is accessible.

        Implementations backed by a local path check that path. A remote
        source has nothing local to check, so it may require the caller to
        name a probe target — ``Context7Client.check_connection`` takes a
        ``library_id`` — rather than hard-coding one library.

        Returns:
            True if connection is successful
        """


class MCPError(Exception):
    """Base exception for MCP client errors."""


class MCPConnectionError(MCPError):
    """Raised when connection to MCP server fails."""


class MCPLibraryNotFoundError(MCPError):
    """Raised when requested library is not found."""
