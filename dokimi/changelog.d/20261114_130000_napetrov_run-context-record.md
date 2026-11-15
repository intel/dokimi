<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`run_context.json` beside each repo run's `events.jsonl`** — one `run_context.v1` record per cell, taken from what the agent CLI printed: its version, the model it started with and the models that answered, effort, credential kind, permission mode, allowed and disallowed tools, plugins, skills, MCP servers, agents, and how the run ended (stop reason, turns, refused tool calls, safety stops). A field the CLI did not print is `null`. Config files appear as SHA-256 digests only. Schema in `dokimi/schemas/run_context.v1.json`, contract in `docs/repo-evaluation-contract.md` §4.9. Codex and Copilot fill fewer fields than Claude Code; no existing output changes.
