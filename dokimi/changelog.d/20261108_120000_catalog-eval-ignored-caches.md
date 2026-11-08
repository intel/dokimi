### Fixed

- **`catalog eval` no longer selects every skill for a tool cache git ignores** — an ignored file under a shared evaluator directory (`tools/`, `scripts/`, `dokimi/`, `.github/`), such as `.pytest_cache` or `.ruff_cache`, was a shared input: it selected every skill and marked the run dirty. Paths git ignores now select nothing there, whatever their name. Ignored files inside a skill or task bundle still count, since the bundle hashes them.
