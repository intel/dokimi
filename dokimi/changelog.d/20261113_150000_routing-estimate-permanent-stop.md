### Fixed

- `skill routing --estimate-only` and `catalog routing --estimate-only` size the selection prompt at 2.6 characters per token and a reply at 150 tokens (it was chars / 4 and 65, about 1.55x below measured bills), and print both allowances. (#676)
- `skill routing` and `catalog routing` stop at the first authentication, permission, unknown-model, unsupported-parameter or no-credentials error (with `claude-cli`, also not logged in, a usage limit or no `claude` binary) instead of failing every remaining call; the result is partial, with `reason` `permanent_error` and the error, and the command exits 1 (a `skill routing` run whose every call failed exited 0 with status `ok`). Any other failure, such as a request too long for the model, is still one failed row. (#677)
