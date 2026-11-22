### Fixed

- **`benchmark preflight` refuses an unreadable `file:` key on a `vertex_ai/` or `bedrock/` route.** `ANTHROPIC_API_KEY=file:/nonexistent/key` with `--provider anthropic --model vertex_ai/gemini-2.5-pro` passed preflight with exit 0 although the first model call fails on the key; it exits 2 now, as it does for any other route. An unset key on such a route is still no refusal.
