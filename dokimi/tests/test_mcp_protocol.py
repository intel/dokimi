# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for the MCP-protocol doc client and its factory wiring."""

import builtins

import pytest

from dokimi.mcp.factory import create_doc_source_client
from dokimi.mcp.mcp_protocol import MCPProtocolClient, _require_mcp
from dokimi.mcp import MCPConnectionError


# ── factory parsing ─────────────────────────────────────────────────────
def test_factory_parses_stdio_ref():
    client = create_doc_source_client("mcp:cmd=npx -y @upstash/context7-mcp")
    assert isinstance(client, MCPProtocolClient)
    assert client.transport == "stdio"
    assert client.command == "npx"
    assert client.args == ["-y", "@upstash/context7-mcp"]


def test_factory_parses_http_ref_with_options():
    client = create_doc_source_client(
        "mcp:http=https://mcp.context7.com/mcp;tool=get-library-docs;id=uxlfoundation/oneTBB"
    )
    assert client.transport == "http"
    assert client.url == "https://mcp.context7.com/mcp"
    assert client.docs_tool == "get-library-docs"
    assert client.default_library_id == "uxlfoundation/oneTBB"


def test_factory_parses_sse_ref():
    client = create_doc_source_client("mcp:sse=https://example.com/sse")
    assert client.transport == "sse"
    assert client.url == "https://example.com/sse"


def test_factory_rejects_bad_ref():
    with pytest.raises(ValueError):
        create_doc_source_client("mcp:bogus")  # no '='
    with pytest.raises(ValueError):
        create_doc_source_client("mcp:ftp=foo")  # unknown transport


def test_constructor_validates_transport_args():
    with pytest.raises(ValueError):
        MCPProtocolClient(transport="stdio")  # missing command
    with pytest.raises(ValueError):
        MCPProtocolClient(transport="http")  # missing url


# ── helpers ──────────────────────────────────────────────────────────────
def test_pick_tool_by_hint():
    tools = ["resolve-library-id", "get-library-docs", "ping"]
    assert MCPProtocolClient._pick_tool(tools, None, ("get-library-docs",)) == "get-library-docs"
    # explicit but absent -> None
    assert MCPProtocolClient._pick_tool(tools, "nope", ("docs",)) is None
    # explicit present
    assert MCPProtocolClient._pick_tool(tools, "ping", ()) == "ping"


def test_result_text_flattens_blocks():
    class _Block:
        def __init__(self, text):
            self.text = text

    class _Result:
        content = [_Block("part one"), _Block("part two")]

    assert MCPProtocolClient._result_text(_Result()) == "part one\n\npart two"
    # dict form
    assert MCPProtocolClient._result_text({"content": [{"text": "x"}]}) == "x"
    # empty
    assert MCPProtocolClient._result_text(object()) == ""


def test_resolve_library_id_uses_default_without_sdk():
    """With a fixed id configured, resolve must not touch the SDK."""
    client = MCPProtocolClient(transport="http", url="http://x", default_library_id="fixed/id")
    assert client.resolve_library_id("whatever") == "fixed/id"


def test_require_mcp_raises_helpful_error(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(MCPConnectionError) as exc:
        _require_mcp()
    assert "pip install mcp" in str(exc.value)


def test_check_connection_false_without_sdk(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    client = MCPProtocolClient(transport="stdio", command="npx")
    assert client.check_connection() is False


# ── streamable HTTP across mcp SDK versions ─────────────────────────────
def _fake_mcp(monkeypatch, http_attrs, streams):
    """Install a fake ``mcp`` SDK whose streamable-HTTP client names ``http_attrs`` yield ``streams``."""
    import sys
    import types
    from contextlib import asynccontextmanager

    class FakeSession:
        def __init__(self, read, write):
            assert (read, write) == ("r", "w")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            pass

        async def list_tools(self):
            return types.SimpleNamespace(tools=[types.SimpleNamespace(name="get-library-docs")])

    @asynccontextmanager
    async def http_client(url):
        yield streams

    mcp = types.ModuleType("mcp")
    mcp.ClientSession = FakeSession
    client = types.ModuleType("mcp.client")
    streamable_http = types.ModuleType("mcp.client.streamable_http")
    for attr in http_attrs:
        setattr(streamable_http, attr, http_client)
    for name, mod in (
        ("mcp", mcp),
        ("mcp.client", client),
        ("mcp.client.streamable_http", streamable_http),
    ):
        monkeypatch.setitem(sys.modules, name, mod)


@pytest.mark.parametrize(
    "http_attrs, streams",
    [
        (["streamable_http_client"], ("r", "w")),  # mcp 2.x: old name removed, yields two streams
        (
            ["streamable_http_client", "streamablehttp_client"],
            ("r", "w", lambda: None),
        ),  # mcp 1.24+
        (["streamablehttp_client"], ("r", "w", lambda: None)),  # older mcp 1.x
    ],
)
def test_http_transport_works_across_mcp_versions(monkeypatch, http_attrs, streams):
    _fake_mcp(monkeypatch, http_attrs, streams)
    client = MCPProtocolClient(transport="http", url="http://x/mcp")
    assert client._list_tool_names() == ["get-library-docs"]
