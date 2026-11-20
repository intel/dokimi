### Fixed

- **`trace consistency` no longer counts a blocked Stop hook as a tool call** — `tool_calls_*` and `failed_calls_mean` now count only tool calls, as `docs/run-consistency.md` says and as `trace inspect` does; the `Hook:Stop` step stays in the path.
