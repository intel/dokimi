<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

Notable changes to dokimi, by release: what to know when choosing or upgrading
a version. The full history is in the Git log and the pull requests.

> **Pre-1.0:** commands, flags, defaults and output fields can change between
> minor releases (0.1 → 0.2). Pin the version you evaluate with, and read the
> release's `Changed` and `Removed` entries before upgrading: a changed default
> or metric can move scores for the same inputs.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html), as set
out in [docs/releasing.md](docs/releasing.md).

## [Unreleased]

Pending changes are fragments in [`changelog.d/`](changelog.d/), collected
here when a version is released.

<!-- scriv-insert-here -->

<a id='changelog-0.1.0'></a>
## [0.1.0] — 2026-10-19

First release on PyPI. Requires Python 3.11 to 3.14.

### Added

- `dokimi --version`; runs and scorecards record `dokimi_version`.
- `tools preference`: how much one change to a tool moves a model's choice between two equal tools; `--max-cost-usd` caps its spend.
- `repo compare`: treatment arms against a baseline arm, paired by task.
- `context trace`: prompt size of every model call in a Claude Code trace.
- `novelty --max-usd` spend cap; `novelty routing --catalog` tests against the real sibling skills.
- Guidance-only repo manifests: `repo scan`, `reach` and `validate` without tasks.
- `repo run --build-timeout` and `repo tasks check --build-timeout` (default 3 h).
- Arms artifacts store the exact text the judge read; `eval cross-judge --judges` re-scores on it.
- Novelty `bundle` records name what was measured (`artifact_name`, `artifact_ref`, `measured_digest`).
- Documentation site at https://intel.github.io/dokimi/.

### Changed

- Default models: answers and generation `openai/gpt-6.1-sol`, judge `anthropic/claude-sonnet-5-5`, on every command. Scores differ from earlier defaults.
- `--judge-provider` alone picks that provider's default judge model; `google`, `google-vertex` and `openrouter` require `--judge-model`; `openai-codex` is no longer offered.
- Question sets are written as `questions.v2` (`question` instead of `text`); v1 sets still load.
- A golden set under `--questions-root` is answered as-is: no question generation, no persona discovery.
- Every judge path refuses a record with no question text before the first judge call (exit 2).
- `eval panel-score` checks numeric claims the way the single judge does. Panel scores differ from earlier runs.
- `harmful_atoms` reports `measured: false` when the conflict check did not cover every atom pair.
- `command:`, Harbor, BenchFlow and explorer timeouts kill the whole process tree.
- Bundled registries and the spec schema ship inside the package.
- RAGAS is an optional extra: `eval ragas` and `answers generate --ragas` need `pip install 'dokimi[ragas]'`, and exit 1 before any model call without it.
- `beautifulsoup4` and `anthropic` are no longer direct dependencies.

### Removed

- Python 3.10.
- The top-level `cli` module is no longer installed; use `dokimi`, `python -m dokimi` or `dokimi.cli`.

### Fixed

- `eval panel-score` judged every answer with an empty question and no context. Panel scores differ from earlier runs.
- Judge and panel replies with non-finite or out-of-range scores are rejected and not cached.
- The trust gate's retrieval hit rate read a field nothing wrote, so it reported 100% for runs with no documents.
- `tasks run` could score a failed cell as passed (relative `--output-dir`, or a stale run dir that could not be cleared).
- Question id `0` was read as missing.
- The agentic arm ran a tool on `{}` when its arguments did not parse; failed calls are now recorded.
- Novelty no longer counts truncated or failed replies, non-atom rows or non-boolean verdicts as results.
