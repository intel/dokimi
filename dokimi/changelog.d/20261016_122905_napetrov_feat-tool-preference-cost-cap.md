### Fixed

- **Tool-preference cost cap keeps billings from all failures** — when a call fails after the model returns a response but before the tool names are parsed, the usage is now captured even if the exception carries no usage attribute, so the cap can track the actual spend. A cost cap that receives `--max-cost-usd 0.02` now ensures the spend stays at or below that bound, even when a billed reply fails with a parse error.
- **Non-dict usage guards on success path** — when a tool call returns a non-dict usage value (e.g., a string or number instead of the expected dict), it is now converted to `None` on the success path (matching exception-path behavior), so the payload does not crash when filtering usage for cost calculations.
- **Documentation on partial groups** — the docstring and user guide were clarified to note that partial groups can occur at any concurrency level, not only when `--concurrency > 1`.
