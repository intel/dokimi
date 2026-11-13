### Fixed

- `novelty routing --estimate-only` sizes the selection prompt at 2.6 characters per token and a reply at 150 tokens (it was chars / 4 and 65, about 1.55x below measured bills), and prints both allowances. (#676)
- `novelty routing` stops at the first permanent provider error (authentication, permission, unknown model, no credentials) instead of failing every remaining call; the result is partial, with the error and the call it stopped at. A failure of one query is still one failed row. (#677)
