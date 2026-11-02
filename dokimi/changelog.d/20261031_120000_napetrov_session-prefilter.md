<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`context prefilter`** — reads Claude Code session logs (`~/.claude/projects/<project>/*.jsonl`) and reports, per rule of an AGENTS.md or CLAUDE.md, how often the situation the rule describes came up, whether the agent ran the command or touched the path it names, and how often the user pushed back on it. The result is a ranked list of candidates for ablation, not a verdict: a rule that is rare or carries a protection tag (a negation, version gate, ordering or threshold) is listed but not ranked. Deterministic, no model call, no transcript text printed; `--json` gives the full report. Goose session stores (`sessions.db`) are read too. See [session-prefilter.md](docs/session-prefilter.md).
