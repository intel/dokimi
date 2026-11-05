<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`guidance:overlay` arm for per-agent AGENTS.md overlays** — `dokimi repo run --arm guidance:overlay` gives a cell the base `AGENTS.md` and the `AGENTS.<agent>.md` of the harness under test, and removes the overlays of other agents (agentsmd/agents.md#185). A harness profile names its agent with the new `overlay` key (`claude` for Claude Code, `codex`, `copilot`). The arm refuses to run without that key, or when the base has no overlay for the agent. `dokimi repo reach` now probes every `AGENTS.<name>.md`, and reports a foreign overlay that the harness loaded as a `finding:` line and in the `overlays` block of `guidance_reach.v1`. Its `own_loaded` field says whether the harness loads its own overlay at all.

### Changed

- **`guidance:none` also removes the agents' overlays** — a `guidance:none` cell now deletes every `AGENTS.claude.md`, `AGENTS.codex.md` and `AGENTS.copilot.md` as well as the files the guidance globs match; a `guidance:as-skill` cell drops the same overlays and does not package them. The cell's execution id is unchanged, so on a repository with overlays a `guidance:none` or `guidance:as-skill` result is not comparable with one from an earlier version: start a new `--batch` (or re-run with `--force`) rather than resuming an old one. `repo tasks replay` now leaves overlays out of `change.patch` as it does guidance files; a replay task built earlier from a PR that edits an overlay fails to prepare under `guidance:none` and `guidance:as-skill`, and under `guidance:overlay` when the edited overlay is another agent's, until it is rebuilt with `--force`.
