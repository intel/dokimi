<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`codex-cli` and `copilot-cli` are answering providers** — the OpenAI Codex CLI and the GitHub Copilot CLI, signed in on the machine, answer a question set on a comparison. Each call runs in a fixed empty directory outside any repository, with no user instructions or project docs and no conversation. `claude-cli` and `copilot-cli` run with no tools; `codex-cli` keeps its read-only sandbox, so a Codex comparison can read files, and its scores are not tool-free. Copilot records no token counts and no cost. `--model` is required for both. See [docs/knowledge-runs.md](docs/knowledge-runs.md).
- **`codex-cli` is refused on a knowledge run** — `answers generate --doc-source none` and `arms run --arms baseline` exit 2 before any call with `--provider codex-cli`: Codex has no switch that turns its tools off, so its answers could read host files. Use `copilot-cli` or `claude-cli`.
- **A knowledge run is stamped and read as one** — `answers generate` stamps `run_intent` (`knowledge` or `paired`) on the answers file, `eval score` carries it into the eval, and `report eval` writes the one-condition page only for a run stamped as a knowledge run. An unstamped file with no retrieval hit still reads as one; a paired run whose retrieval missed every question gets the paired report.
- **`terminated` on a run that a usage limit, a missing login or a rejected command line stopped** — the `answers.v1` and `arms.v1` files record the reason and the questions requested and recorded.
- **`report eval` on a one-condition (knowledge) run writes a single-condition page** — one row for `without_docs (knowledge)` with its mean, lowest and highest score, by difficulty, the lowest-scoring questions, and every question with its score. Paired runs keep the paired report.

### Changed

- **`claude-cli` stops at a usage limit or missing login** — `answers generate` and `arms run` keep what was answered, save it, and exit 1; `evaluate` and `benchmark run` do not judge or report a stopped answers file as a complete run.
- **The judge follows a CLI answerer only on `claude-cli`** — `codex-cli` and `copilot-cli` answerers keep the API default judge unless `--judge-provider` names a CLI.
- **Per-question diagnosis of a knowledge run is `⚪ One condition (no delta)`** — `eval score pointwise` no longer reports `insufficient_data` for a question that has one condition and no context answer.
