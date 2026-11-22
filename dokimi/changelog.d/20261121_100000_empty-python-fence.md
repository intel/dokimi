### Fixed

- **`repo scan` no longer raises on an empty code fence** — a README or AGENTS.md with an empty `python` or `starlark` fence (the opening fence line followed directly by the closing one) made the scan fail with `ValueError: zip() argument 2 is longer than argument 1`; such a fence is now skipped.
