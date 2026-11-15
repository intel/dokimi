<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`events.jsonl` has a `turn` event per model call and a `compaction` event per compaction.**
  A Claude run's turns come from the parse its token totals use, so their sums are the run's
  `input_tokens`, `output_tokens`, `cache_read_tokens` and `cache_write_tokens`; a subagent's
  turns carry `agent`. Codex reports a turn per `turn.completed`, with a count it did not report
  as `null`; Copilot's trace has no usage, so it writes no turn events. `process.tool_calls` counts
  the tool events only. The format is documented in `docs/events-format.md`.
