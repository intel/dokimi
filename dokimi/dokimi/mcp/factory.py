# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Factory for creating documentation source clients from CLI flag values."""

from pathlib import Path
from typing import Optional

from . import MCPClient


def create_doc_source_client(
    doc_source: str,
    cache_dir: Optional[Path] = None,
) -> MCPClient:
    """
    Create an MCP client from a ``--doc-source`` string.

    Supported formats
    -----------------
    ``context7``
        Use the Context7 cloud API (default).  Requires internet access.

    ``local:<path>``
        Load documentation from a local directory or file.
        ``<path>`` may be absolute or relative to the current working directory.
        Supports ``.md``, ``.rst``, ``.txt``, ``.html`` and ``.htm`` files.

    ``url:<url>``
        Fetch documentation from an arbitrary URL, split into paragraphs,
        and return the most query-relevant chunks.

    ``okf:<path>``
        Load a Google OKF (Open Knowledge Format) bundle: a directory of
        Markdown files with YAML frontmatter.  The frontmatter is scored as
        additional retrieval signal and passed through as each chunk's
        ``metadata``.  ``<path>`` may be absolute or relative.

    ``mcp:<ref>``
        Retrieve documentation through a real MCP server (tool-call protocol).
        ``<ref>`` selects the transport and target, with optional ``;``-separated
        options::

            mcp:cmd=npx -y @upstash/context7-mcp
            mcp:http=https://mcp.context7.com/mcp;tool=get-library-docs
            mcp:sse=https://example.com/sse;id=uxlfoundation/oneTBB

        Options: ``tool=`` (docs tool), ``resolve=`` (resolve tool), ``id=``
        (fixed library id). Requires ``pip install mcp``.

    Parameters
    ----------
    doc_source:
        Source descriptor string (see formats above).
    cache_dir:
        Optional cache directory.  Used by Context7Client and URLClient.
        Defaults to ``.cache/<source-type>``.

    Returns
    -------
    MCPClient
        A fully-configured client ready to call ``get_library_docs()``.

    Raises
    ------
    ValueError
        If *doc_source* is not recognised.

    Examples
    --------
    >>> client = create_doc_source_client("context7")
    >>> client = create_doc_source_client("local:/docs/onetbb")
    >>> client = create_doc_source_client("url:https://spec.example.com/api.html")
    >>> client = create_doc_source_client("okf:/docs/onetbb-okf-bundle")
    """
    if doc_source == "context7":
        from .context7 import Context7Client
        _cache = cache_dir or Path(".cache/context7")
        return Context7Client(cache_dir=_cache)

    if doc_source.startswith("local:"):
        from .local_markdown import LocalMarkdownClient
        path_str = doc_source[len("local:"):]
        if not path_str:
            raise ValueError("'local:' source requires a path, e.g. 'local:/path/to/docs'")
        return LocalMarkdownClient(Path(path_str))

    if doc_source.startswith("url:"):
        from .url_client import URLClient
        url = doc_source[len("url:"):]
        if not url:
            raise ValueError("'url:' source requires a URL, e.g. 'url:https://example.com/docs'")
        _cache = cache_dir or Path(".cache/url")
        return URLClient(url=url, cache_dir=_cache)

    if doc_source.startswith("okf:"):
        from .okf_client import OKFClient
        path_str = doc_source[len("okf:"):]
        if not path_str:
            raise ValueError("'okf:' source requires a path, e.g. 'okf:/path/to/okf-bundle'")
        return OKFClient(Path(path_str))

    if doc_source.startswith("mcp:"):
        return _build_mcp_protocol_client(doc_source[len("mcp:"):])

    raise ValueError(
        f"Unknown --doc-source format: '{doc_source}'. "
        "Valid formats: 'context7', 'local:<path>', 'url:<url>', 'okf:<path>', 'mcp:<ref>'"
    )


def resolve_library_id_for_product(
    product: str,
    client: MCPClient,
    explicit_id: Optional[str] = None,
    registry=None,
) -> str:
    """Resolve the doc-source library id to use for *product*.

    Three steps, in order:

    1. ``explicit_id`` — whatever the caller passed as ``--context7-id``.
    2. ``context7_id`` declared for the product in ``products.yaml``, when the
       product is registered (registry lookup is case-insensitive, so
       ``oneTBB`` finds the ``onetbb`` entry). A registered product that
       declares none is an error for any client with
       ``requires_declared_library_id``: a registry key is not an id, and a key
       such as ``mkl_extensions`` would otherwise pass Context7's website-form
       check verbatim and retrieve nothing.
    3. the client's own ``resolve_library_id``, for a name that is not a
       registry key at all. Local sources return their own path;
       :class:`~dokimi.mcp.context7.Context7Client` raises
       ``ValueError`` for an unqualified name, naming both options above.

    Args:
        product: Product name or registry key.
        client: Doc-source client to fall back to.
        explicit_id: Library id given on the command line, if any.
        registry: Registry to consult; defaults to the bundled
            ``products.yaml`` via :class:`ProductRegistry`.
    """
    if explicit_id:
        return explicit_id

    if registry is None:
        from dokimi.registry import ProductRegistry
        registry = ProductRegistry()

    if product in registry:
        declared = registry.get(product).context7_id
        if declared:
            return declared
        if getattr(client, "requires_declared_library_id", False):
            raise ValueError(
                f"product '{product}' is registered but declares no "
                "'context7_id', and a registry key is not a Context7 library "
                "id (Context7 ids look like 'uxlfoundation/oneTBB' or "
                "'intelpython_github_io_dpnp'). Either add 'context7_id:' to "
                f"the '{product}' entry in products.yaml, or pass "
                "--context7-id on the command line."
            )

    return client.resolve_library_id(product)


def _build_mcp_protocol_client(ref: str):
    """Parse an ``mcp:`` reference into an MCPProtocolClient.

    Grammar: ``<transport>=<target>[;key=value...]`` where transport is one of
    ``cmd`` (stdio), ``http``, or ``sse``. Recognised options: ``tool``,
    ``resolve``, ``id``.
    """
    from .mcp_protocol import MCPProtocolClient

    parts = ref.split(";")
    head = parts[0].strip()
    options = {}
    for opt in parts[1:]:
        if "=" in opt:
            k, _, v = opt.partition("=")
            options[k.strip()] = v.strip()

    if "=" not in head:
        raise ValueError(
            f"Invalid mcp ref: '{ref}'. Expected 'cmd=<command>', 'http=<url>', "
            "or 'sse=<url>'."
        )
    transport_key, _, target = head.partition("=")
    transport_key = transport_key.strip()
    target = target.strip()

    docs_tool, resolve_tool, library_id = options.get("tool"), options.get("resolve"), options.get("id")

    if transport_key == "cmd":
        argv = target.split()
        if not argv:
            raise ValueError("mcp 'cmd=' requires a command, e.g. 'cmd=npx -y @upstash/context7-mcp'")
        return MCPProtocolClient(
            transport="stdio", command=argv[0], args=argv[1:],
            docs_tool=docs_tool, resolve_tool=resolve_tool, default_library_id=library_id,
        )
    if transport_key in ("http", "sse"):
        return MCPProtocolClient(
            transport=transport_key, url=target,
            docs_tool=docs_tool, resolve_tool=resolve_tool, default_library_id=library_id,
        )

    raise ValueError(
        f"Unknown mcp transport '{transport_key}' in '{ref}'. "
        "Use 'cmd=', 'http=', or 'sse='."
    )
