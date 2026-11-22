### Fixed

- **`first_prompt_tokens` counts split usage records and leaves an output-only usage unknown** — blocks of one message id that each carry some of `input`, `cache_creation` and `cache_read` now add up (108 became 119 for the issue's trace), and a call whose usage has none of the three reports `null`, not `0`, in `result.json` and `trace inspect`. Closes #1088.
