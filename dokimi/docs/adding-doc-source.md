<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Using Multiple Documentation Sources

By default `dokimi` fetches docs from [Context7](https://context7.com/).
With `--doc-source` you can point it at a local directory or any URL instead.

## Quick reference

| Flag value | What it does |
|---|---|
| `context7` | Context7 cloud API (default) |
| `local:<path>` | Local `.md` / `.rst` / `.html` / `.txt` files |
| `url:<url>` | Fetch and search a single web page |
| `okf:<path>` | Google OKF (Open Knowledge Format) bundle — a directory of Markdown files with YAML frontmatter |
| `mcp:<ref>` | Retrieve through a real MCP server (requires `pip install mcp`) |

### Naming the library for `context7` and `mcp:`

A Context7 library ID is not derivable from a product name, so nothing guesses
one. For `--doc-source context7` (and MCP servers that take a library id) the id
is resolved in this order:

1. `--context7-id <id>` on the command line, if given;
2. the product's `context7_id:` in the registry (`products.yaml`, or a custom one
   passed with `--registry` — see
   [external-content.md](external-content.md#6-products-this-repository-has-never-heard-of));
3. otherwise the command stops with an error naming both options above.

IDs look like `uxlfoundation/oneTBB` (a GitHub repo) or
`intelpython_github_io_dpnp` (a documentation website). `local:`, `url:` and
`okf:` sources need no id at all.

The `mcp:<ref>` form is `<transport>=<target>[;opt=val...]`, e.g.
`mcp:cmd=npx -y @upstash/context7-mcp`,
`mcp:http=https://mcp.context7.com/mcp;tool=get-library-docs`, or
`mcp:sse=https://example.com/sse;id=uxlfoundation/oneTBB`. To compare an MCP
doc source against a baseline as treatment arms, see
[evaluating-treatments.md](evaluating-treatments.md).

---

## Examples

```bash
# Default — Context7
python cli.py answers generate --product oneTBB --questions questions/oneTBB.json

# Local Sphinx HTML build
python cli.py answers generate --product oneTBB --questions questions/oneTBB.json \
  --doc-source local:/path/to/oneTBB/docs/_build/html

# Remote API reference page
python cli.py answers generate --product oneTBB --questions questions/oneTBB.json \
  --doc-source url:https://spec.oneapi.io/versions/latest/elements/oneTBB/source/nested-index.html

# Full pipeline with local docs
python cli.py evaluate --product oneTBB --repo uxlfoundation/oneTBB \
  --doc-source local:/path/to/docs

# OKF bundle (Markdown + YAML frontmatter)
python cli.py evaluate --product oneTBB \
  --doc-source okf:/path/to/onetbb-okf-bundle
```

`--doc-source` is supported by `answers generate`, `questions generate`, and `evaluate`.

---

## OKF (Open Knowledge Format)

`okf:<path>` reads a Google **Open Knowledge Format** bundle: a directory of
Markdown files, each with a YAML frontmatter block. OKF is vendor-neutral,
git-friendly, and SDK-free — the same shape this repo already uses for its
`data/skills/*/SKILL.md` sources.

The `okf:` client differs from `local:` in that it **parses the frontmatter**:

- Frontmatter fields (`title`, `tags`, `type`, `description`) are scored as
  extra retrieval signal on top of the Markdown body, so a document whose
  frontmatter matches the query ranks higher.
- The parsed frontmatter is passed through unchanged in each chunk's
  `metadata`, so downstream steps can route on `type` (`api`, `runbook`, …).
- Malformed frontmatter degrades to an empty dict rather than aborting the run.

Because OKF is just a doc source, you can benchmark it as a **treatment arm**
against `context7` or raw `local:` markdown to measure whether the OKF format
actually improves agent answers — see
[evaluating-treatments.md](evaluating-treatments.md).

---

## Adding a custom source

Implement the three-method `MCPClient` interface and register it in the factory.

### 1 — Create the client

```python
# dokimi/mcp/confluence.py
import re
from typing import List, Dict, Any, Optional
import httpx
from . import MCPClient, MCPConnectionError

class ConfluenceClient(MCPClient):
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def resolve_library_id(self, library_name: str) -> str:
        return f"confluence:{library_name}"

    def get_library_docs(
        self,
        library_id: str,
        query: str,
        max_results: int = 5,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """Search Confluence and return relevant page excerpts."""
        headers = {"Authorization": f"Bearer {self.token}"}
        resp = httpx.get(
            f"{self.base_url}/rest/api/content/search",
            params={"cql": f'text ~ "{query}"', "limit": max_results},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        pages = resp.json().get("results", [])
        return [
            {
                "content": p["body"]["storage"]["value"][:max_tokens * 4],
                "source": "confluence",
                "url": f"{self.base_url}/wiki{p['_links']['webui']}",
                "library_id": library_id,
                "query": query,
                "relevance_score": 1.0,
            }
            for p in pages
        ]

    def check_connection(self) -> bool:
        try:
            r = httpx.head(self.base_url, timeout=10)
            return r.status_code < 400
        except Exception:
            return False
```

### 2 — Register in the factory

```python
# dokimi/mcp/factory.py  (add inside create_doc_source_client)

    if doc_source.startswith("confluence:"):
        from .confluence import ConfluenceClient
        import os
        url, _, space = doc_source[len("confluence:"):].partition("/")
        return ConfluenceClient(
            base_url=url,
            token=os.environ["CONFLUENCE_TOKEN"],
        )
```

### 3 — Use it

```bash
CONFLUENCE_TOKEN=xxx python cli.py answers generate \
  --product myLib \
  --questions questions/myLib.json \
  --doc-source confluence:https://wiki.example.com
```

---

## How relevance scoring works

`LocalMarkdownClient` and `URLClient` use a lightweight keyword-overlap score:

```text
score = (query tokens found in chunk) / (total query tokens)
```

Chunks are ranked by this score and the top `max_results` are returned.
The Context7 client delegates ranking to the Context7 API (topic-based retrieval).
