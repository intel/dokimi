<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **Knowledge runs are documented as one named way to ask one question set of one answerer and grade it** — [docs/knowledge-runs.md](docs/knowledge-runs.md): `answers generate --doc-source none` with `eval score reference`, or `arms run --arms baseline` (a one-arm `arms.v1`), with the agent CLI `--provider claude-cli` as an answerer. Run it before any with-docs comparison.

### Fixed

- **`arms run --harness <task harness>` is refused** — a task-harness label (`codex`, `claude-code`, `copilot`, `command:*`, `terminal-bench:*`, `docker-*`, `benchflow*`) stamped answers that the model behind `--provider` produced. It now exits 2 and points to `--provider claude-cli` or dropping `--harness`.
- **`answers generate --doc-source none` no longer prints a `Context arm` line** — with no retrieval there is no context arm to count.
