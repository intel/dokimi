### Fixed

- **`mcp:http=` doc sources work with mcp 2.x** — the MCP-protocol client
  imported `streamablehttp_client`, which mcp 2.x removed, so with the `[mcp]`
  extra resolving to mcp 2.x every `http` doc source failed with an
  `ImportError`. It now uses `streamable_http_client` (mcp 1.24+ and 2.x),
  falls back to the old name on older 1.x, and accepts both the 1.x
  three-stream and 2.x two-stream shapes.
