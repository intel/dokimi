# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Documentation retrieval through a real MCP server (tool-call protocol).

Unlike :mod:`dokimi.mcp.context7` (which talks plain HTTP), this client
speaks the Model Context Protocol: it opens a session to an MCP server, lists
its tools, and calls a documentation-retrieval tool, mapping the result back
into the standard chunk shape used across the benchmark.

The ``mcp`` Python SDK is an optional dependency and is imported lazily, so the
rest of the package keeps working without it. Install with ``pip install mcp``.

Transports
----------
``stdio``  Launch a local MCP server process (``command`` + ``args``).
``http``   Connect to a streamable-HTTP MCP endpoint (``url``).
``sse``    Connect to an SSE MCP endpoint (``url``).
"""

import logging
from typing import Any, Dict, List, Optional

from . import MCPClient, MCPConnectionError

logger = logging.getLogger(__name__)

# Tool-name fragments we try, in order, when no explicit docs tool is given.
_DOC_TOOL_HINTS = ("get-library-docs", "get_library_docs", "query-docs", "docs", "search")
_RESOLVE_TOOL_HINTS = ("resolve-library-id", "resolve_library_id", "resolve")


def _require_mcp():
    """Import the optional ``mcp`` SDK or raise a helpful error."""
    try:
        import mcp  # noqa: F401
        return mcp
    except ImportError as exc:  # pragma: no cover - exercised via tests with monkeypatch
        raise MCPConnectionError(
            "The 'mcp' package is required for MCP-protocol doc sources. "
            "Install it with 'pip install mcp'."
        ) from exc


class MCPProtocolClient(MCPClient):
    """Retrieve docs from an MCP server via the MCP tool-call protocol."""

    def __init__(
        self,
        transport: str,
        command: Optional[str] = None,
        args: Optional[List[str]] = None,
        url: Optional[str] = None,
        docs_tool: Optional[str] = None,
        resolve_tool: Optional[str] = None,
        default_library_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        if transport not in ("stdio", "http", "sse"):
            raise ValueError(f"Unsupported MCP transport: {transport!r}")
        if transport == "stdio" and not command:
            raise ValueError("stdio transport requires a 'command'")
        if transport in ("http", "sse") and not url:
            raise ValueError(f"{transport} transport requires a 'url'")

        self.transport = transport
        self.command = command
        self.args = args or []
        self.url = url
        self.docs_tool = docs_tool
        self.resolve_tool = resolve_tool
        self.default_library_id = default_library_id
        self.timeout = timeout

    # ── MCPClient interface ─────────────────────────────────────────────
    def resolve_library_id(self, library_name: str) -> str:
        """Resolve a library name to an id, via the server when it offers a tool."""
        if self.default_library_id:
            return self.default_library_id
        try:
            tools = self._list_tool_names()
            resolve = self._pick_tool(tools, self.resolve_tool, _RESOLVE_TOOL_HINTS)
        except Exception:
            resolve = None
        if not resolve:
            return library_name
        try:
            result = self._call_tool(resolve, {"libraryName": library_name})
            text = self._result_text(result).strip()
            return text or library_name
        except Exception:
            logger.exception("resolve_library_id via MCP failed; falling back to name")
            return library_name

    def get_library_docs(
        self,
        library_id: str,
        query: str,
        max_results: int = 5,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """Call the server's docs tool and return standard chunk dicts."""
        tools = self._list_tool_names()
        docs_tool = self._pick_tool(tools, self.docs_tool, _DOC_TOOL_HINTS)
        if not docs_tool:
            raise MCPConnectionError(
                f"No documentation tool found on MCP server "
                f"(have: {tools}). Pass an explicit ';tool=<name>'."
            )
        result = self._call_tool(
            docs_tool,
            {"context7CompatibleLibraryID": library_id, "libraryId": library_id,
             "query": query, "topic": query, "tokens": max_tokens},
        )
        text = self._result_text(result)
        if not text:
            return []
        return [{
            "content": text,
            "source": f"mcp:{docs_tool}",
            "library_id": library_id,
            "query": query,
            "relevance_score": 1.0,
        }]

    def check_connection(self) -> bool:
        try:
            self._list_tool_names()
            return True
        except Exception:
            return False

    # ── MCP session plumbing ────────────────────────────────────────────
    def _run(self, coro):
        """Run an async coroutine to completion in a fresh event loop."""
        import asyncio

        return asyncio.run(coro)

    def _open_session(self):
        """Return an async context manager yielding a connected ClientSession."""
        _require_mcp()
        from contextlib import asynccontextmanager
        from mcp import ClientSession

        transport = self.transport
        command, args, url = self.command, self.args, self.url

        @asynccontextmanager
        async def _ctx():
            if transport == "stdio":
                from mcp import StdioServerParameters
                from mcp.client.stdio import stdio_client
                params = StdioServerParameters(command=command, args=args)
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
            elif transport == "http":
                try:
                    # mcp >= 1.24; mcp 2.x removed the old streamablehttp_client name
                    from mcp.client.streamable_http import streamable_http_client as http_client
                except ImportError:
                    from mcp.client.streamable_http import streamablehttp_client as http_client
                # mcp 1.x yields (read, write, get_session_id); mcp 2.x yields (read, write)
                async with http_client(url) as streams:
                    read, write = streams[0], streams[1]
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
            else:  # sse
                from mcp.client.sse import sse_client
                async with sse_client(url) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session

        return _ctx()

    def _list_tool_names(self) -> List[str]:
        async def _run():
            async with self._open_session() as session:
                resp = await session.list_tools()
                return [t.name for t in resp.tools]

        return self._run(_run())

    def _call_tool(self, name: str, arguments: Dict[str, Any]):
        async def _run():
            async with self._open_session() as session:
                return await session.call_tool(name, arguments=arguments)

        return self._run(_run())

    @staticmethod
    def _pick_tool(tools: List[str], explicit: Optional[str], hints) -> Optional[str]:
        if explicit:
            return explicit if explicit in tools else None
        lowered = {t.lower(): t for t in tools}
        for hint in hints:
            for low, original in lowered.items():
                if hint in low:
                    return original
        return None

    @staticmethod
    def _result_text(result) -> str:
        """Flatten an MCP tool result's content blocks into text."""
        content = getattr(result, "content", None)
        if content is None and isinstance(result, dict):
            content = result.get("content")
        if content is None:
            return ""
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(text)
        return "\n\n".join(parts)
