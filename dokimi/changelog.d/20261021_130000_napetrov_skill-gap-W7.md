<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **Multi-turn session measurement for `arms run`** — `--session-turns N` asks each question as its own text followed by the first N-1 entries of its `follow_ups` list, keeps the conversation history across the turns, and records each turn's input, output, cache read and write tokens and cost under `session.turns` of the arm. The arm's `token_usage` and `metrics` are the sums over turns, so cost rollups read them as before. `--turn-reminder TEXT` also runs each arm as `<arm>+reminder`, with the text appended to every user turn in a `<system-reminder>` block, to test whether a per-turn reminder keeps an instruction in force in later turns. Measurement only: `--judge` is refused with `--session-turns` and every `eval` judge command refuses a session artifact, a question with too few `follow_ups` fails the run before any answer is paid for, and agentic arms (`mcp:`, `skill-agent:`, `skill-catalog:`) are refused per arm. Without the flags, `arms run` behaves as before.
