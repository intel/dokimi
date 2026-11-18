<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`events.jsonl` has a `turn` event per model call and a `compaction` event per compaction.**
  A Claude run's turns come from its assistant records; their sums should match the run's
  `input_tokens`, `cache_read_tokens` and `cache_write_tokens` when the result's usage equals its
  per-call usage, and their `output` is `null` (the records carry the output count from the start
  of the stream, not the billed one); a subagent's turns carry `agent`. Codex reports a turn per `turn.completed`, with a count it did not report
  as `null`; Copilot's trace has no usage, so it writes no turn events. `process.tool_calls` counts
  the tool events only. Lines are format version 2 (`repo_event.v2.json`): `output` and `output_truncated` sit on `command` lines only; `repo_event.v1.json` is unchanged. The format is documented in `docs/events-format.md`.
