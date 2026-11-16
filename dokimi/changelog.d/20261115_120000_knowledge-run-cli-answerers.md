<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`codex-cli` and `copilot-cli` are answering providers** — the OpenAI Codex CLI and the GitHub Copilot CLI, signed in on the machine, answer a question set on a comparison; `copilot-cli` also answers a knowledge run (`codex-cli` is refused there, see Fixed). Each call runs in a fixed empty directory outside any repository, with no user instructions or project docs, no tools and no conversation; a usage limit or missing login stops the run. Copilot records no token counts and no cost. See [docs/knowledge-runs.md](docs/knowledge-runs.md).
- **`report eval` on a one-condition (knowledge) run writes a single-condition page** — one row for `without_docs (knowledge)` with its mean, lowest and highest score, by difficulty, the lowest-scoring questions, and every question with its score. Paired runs keep the paired report.

### Fixed

- **`codex-cli` is refused on a knowledge run** — `answers generate --doc-source none` and `arms run --arms baseline` exit 2 before any call with `--provider codex-cli`: Codex has no switch that turns its tools off, so its answers could read host files. Use `copilot-cli` or `claude-cli`.
- **A paired run is not reported as a knowledge run by accident** — `report eval` writes the one-condition page only when the run is stamped as a knowledge run (`run_intent`, set by `answers generate` and carried into the eval); an unstamped file with no retrieval hit still reads as one, and a paired run whose retrieval missed every question gets the paired report.

### Changed

- **Per-question diagnosis of a knowledge run is `⚪ One condition (no delta)`** — `eval score pointwise` no longer reports `insufficient_data` for a question that has one condition and no context answer.
