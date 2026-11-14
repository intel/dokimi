### Fixed

- `novelty routing --estimate-only` sizes the selection prompt at 2.6 characters per token and a reply at 150 tokens (it was chars / 4 and 65, about 1.55x below measured bills), and prints both allowances. (#676)
- `novelty routing` stops at the first authentication, permission, unknown-model, unsupported-parameter or no-credentials error (with `claude-cli`, also not logged in, a usage limit or no `claude` binary) instead of failing every remaining call; the result is partial, with `reason` `permanent_error` and the error. Any other failure, such as a request too long for the model, is still one failed row. (#677)
