# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Tests for create_doc_source_client factory."""

import pytest
from pathlib import Path

from dokimi.mcp.factory import create_doc_source_client
from dokimi.mcp.context7 import Context7Client
from dokimi.mcp.local_markdown import LocalMarkdownClient
from dokimi.mcp.url_client import URLClient
from dokimi.mcp.okf_client import OKFClient


def test_context7_default(tmp_path):
    client = create_doc_source_client("context7", cache_dir=tmp_path)
    assert isinstance(client, Context7Client)


def test_context7_creates_cache_dir(tmp_path):
    cache = tmp_path / "ctx7cache"
    client = create_doc_source_client("context7", cache_dir=cache)
    assert isinstance(client, Context7Client)
    assert cache.exists()


def test_local_source(tmp_path):
    client = create_doc_source_client(f"local:{tmp_path}")
    assert isinstance(client, LocalMarkdownClient)
    assert client.path == tmp_path


def test_local_source_relative_path():
    client = create_doc_source_client("local:docs/onetbb")
    assert isinstance(client, LocalMarkdownClient)
    assert client.path == Path("docs/onetbb")


def test_url_source(tmp_path):
    url = "https://example.com/api-docs"
    client = create_doc_source_client(f"url:{url}", cache_dir=tmp_path)
    assert isinstance(client, URLClient)
    assert client.url == url


def test_url_source_no_cache():
    client = create_doc_source_client("url:https://example.com/docs")
    assert isinstance(client, URLClient)


def test_okf_source(tmp_path):
    client = create_doc_source_client(f"okf:{tmp_path}")
    assert isinstance(client, OKFClient)
    assert client.path == tmp_path


def test_okf_source_relative_path():
    client = create_doc_source_client("okf:docs/onetbb-okf")
    assert isinstance(client, OKFClient)
    assert client.path == Path("docs/onetbb-okf")


def test_okf_missing_path_raises():
    with pytest.raises(ValueError, match="okf:.*path"):
        create_doc_source_client("okf:")


def test_local_missing_path_raises():
    with pytest.raises(ValueError, match="local:.*path"):
        create_doc_source_client("local:")


def test_url_missing_url_raises():
    with pytest.raises(ValueError, match="url:.*URL"):
        create_doc_source_client("url:")


def test_unknown_source_raises():
    with pytest.raises(ValueError, match="Unknown.*doc-source"):
        create_doc_source_client("s3://bucket/docs")


def test_unknown_source_plain_string_raises():
    with pytest.raises(ValueError):
        create_doc_source_client("confluence")


def test_returns_mcp_client_interface():
    """All returned clients must satisfy the MCPClient interface."""
    import tempfile
    from dokimi.mcp import MCPClient

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "test.md").write_text("# Test\n\nSome content here.")
        sources = [
            "context7",
            f"local:{tmp_path}",
            "url:https://example.com/docs",
            f"okf:{tmp_path}",
        ]
        for source in sources:
            client = create_doc_source_client(source)
            assert isinstance(client, MCPClient), f"{source} did not return MCPClient"
            assert hasattr(client, "resolve_library_id")
            assert hasattr(client, "get_library_docs")
            assert hasattr(client, "check_connection")


# ── resolve_library_id_for_product ───────────────────────────────────────────

def _demo_registry(tmp_path, context7_id="declared/id"):
    from dokimi.registry import ProductRegistry

    path = tmp_path / "products.yaml"
    body = "products:\n  demo:\n    name: demo\n    description: x\n"
    if context7_id is not None:
        body += f"    context7_id: {context7_id}\n"
    path.write_text(body, encoding="utf-8")
    return ProductRegistry(path=path)


class _StubClient:
    # Mirrors the MCPClient default: this source mints its own ids.
    requires_declared_library_id = False

    def resolve_library_id(self, library_name):
        return f"stub/{library_name}"


def test_explicit_id_wins_over_registry_and_client(tmp_path):
    from dokimi.mcp.factory import resolve_library_id_for_product

    registry = _demo_registry(tmp_path)
    resolved = resolve_library_id_for_product(
        "demo", _StubClient(), explicit_id="manual/id", registry=registry
    )
    assert resolved == "manual/id"


def test_registry_context7_id_wins_over_client(tmp_path):
    from dokimi.mcp.factory import resolve_library_id_for_product

    registry = _demo_registry(tmp_path)
    assert resolve_library_id_for_product("demo", _StubClient(), registry=registry) == "declared/id"
    # The registry lookup is case-insensitive, like ProductRegistry.get.
    assert resolve_library_id_for_product("DEMO", _StubClient(), registry=registry) == "declared/id"


def test_client_resolves_an_unregistered_product(tmp_path):
    from dokimi.mcp.factory import resolve_library_id_for_product

    registry = _demo_registry(tmp_path)
    resolved = resolve_library_id_for_product("other", _StubClient(), registry=registry)
    assert resolved == "stub/other"


def test_registered_product_without_context7_id_falls_through(tmp_path):
    """A client that mints its own ids (local dir, OKF bundle) ignores the name, so a
    registered product with no ``context7_id`` is still resolvable through it."""
    from dokimi.mcp.factory import resolve_library_id_for_product

    registry = _demo_registry(tmp_path, context7_id=None)
    assert _StubClient().requires_declared_library_id is False
    resolved = resolve_library_id_for_product("demo", _StubClient(), registry=registry)
    assert resolved == "stub/demo"


def test_context7_client_raises_for_an_unresolvable_product(tmp_path):
    from dokimi.mcp.factory import resolve_library_id_for_product

    registry = _demo_registry(tmp_path, context7_id=None)
    with pytest.raises(ValueError) as exc_info:
        resolve_library_id_for_product("demo", Context7Client(), registry=registry)
    assert "--context7-id" in str(exc_info.value)


def test_an_underscored_registry_key_is_never_used_as_a_context7_id(tmp_path):
    """Context7's website-ID form is ``host_path``, so an underscored *registry key*
    passes its syntax check verbatim. That is the guess the whole no-guessing contract
    exists to prevent: the id is well formed, does not exist, and the arm reports empty
    retrieval as if the docs had nothing to say. The registry step must stop it first."""
    from dokimi.mcp.factory import resolve_library_id_for_product
    from dokimi.registry import ProductRegistry

    path = tmp_path / "products.yaml"
    path.write_text(
        "products:\n  mkl_extensions:\n    name: MKL extensions\n    description: x\n",
        encoding="utf-8",
    )
    registry = ProductRegistry(path=path)

    # The bare key really does satisfy the client's own syntax check ...
    assert Context7Client().resolve_library_id("mkl_extensions") == "mkl_extensions"
    # ... so the guard has to live where the key is known to be a key.
    with pytest.raises(ValueError) as exc_info:
        resolve_library_id_for_product("mkl_extensions", Context7Client(), registry=registry)
    message = str(exc_info.value)
    assert "mkl_extensions" in message
    assert "context7_id" in message and "--context7-id" in message


def test_no_committed_product_resolves_to_its_own_registry_key():
    """Over the shipped products.yaml: every product either declares a Context7 id or
    fails loudly. None may resolve to its own key."""
    from dokimi.mcp.factory import resolve_library_id_for_product
    from dokimi.registry import ProductRegistry

    registry = ProductRegistry()
    keys = registry.keys()
    assert keys, "products.yaml resolved to zero products; this check would test nothing"
    undeclared = [key for key in keys if not registry.get(key).context7_id]
    assert undeclared, (
        "every committed product now declares context7_id, so this check no longer "
        "exercises the raising path; keep it only while some product does not"
    )
    for key in keys:
        try:
            resolved = resolve_library_id_for_product(key, Context7Client(), registry=registry)
        except ValueError:
            assert key in undeclared, f"{key} declares an id but still raised"
            continue
        assert resolved == registry.get(key).context7_id
        assert resolved != key, f"{key} resolved to its own registry key"


# ── an explicit ``;id=`` on an ``mcp:`` source (#310) ────────────────────────

def test_mcp_source_id_wins_over_registry_context7_id(tmp_path):
    from dokimi.mcp.factory import create_doc_source_client, resolve_library_id_for_product

    client = create_doc_source_client("mcp:http=http://127.0.0.1:9/mcp;id=source/id")
    resolved = resolve_library_id_for_product("demo", client, registry=_demo_registry(tmp_path))
    assert resolved == "source/id"


def test_explicit_id_wins_over_mcp_source_id(tmp_path):
    from dokimi.mcp.factory import create_doc_source_client, resolve_library_id_for_product

    client = create_doc_source_client("mcp:http=http://127.0.0.1:9/mcp;id=source/id")
    resolved = resolve_library_id_for_product(
        "demo", client, explicit_id="manual/id", registry=_demo_registry(tmp_path)
    )
    assert resolved == "manual/id"


def test_mcp_source_without_id_still_uses_registry_context7_id(tmp_path):
    from dokimi.mcp.factory import create_doc_source_client, resolve_library_id_for_product

    client = create_doc_source_client("mcp:http=http://127.0.0.1:9/mcp")
    resolved = resolve_library_id_for_product("demo", client, registry=_demo_registry(tmp_path))
    assert resolved == "declared/id"


def test_mcp_source_id_satisfies_a_registered_product_with_no_context7_id(tmp_path):
    from dokimi.mcp.factory import create_doc_source_client, resolve_library_id_for_product

    client = create_doc_source_client("mcp:http=http://127.0.0.1:9/mcp;id=source/id")
    registry = _demo_registry(tmp_path, context7_id=None)
    assert resolve_library_id_for_product("demo", client, registry=registry) == "source/id"
