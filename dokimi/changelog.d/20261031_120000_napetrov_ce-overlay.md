<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`guidance:overlay` arm for per-agent AGENTS.md overlays** — `dokimi repo run --arm guidance:overlay` gives a cell the base `AGENTS.md` and the `AGENTS.<agent>.md` of the harness under test, and removes the overlays of other agents (agentsmd/agents.md#185). A harness profile names its agent with the new `overlay` key (`claude` for Claude Code, `codex`, `copilot`). The arm refuses to run without that key, or when the base has no overlay for the agent. `dokimi repo reach` now probes every `AGENTS.<name>.md`, and reports a foreign overlay that the harness loaded as a `finding:` line and in the `overlays` block of `guidance_reach.v1`. Its `own_loaded` field says whether the harness loads its own overlay at all.
