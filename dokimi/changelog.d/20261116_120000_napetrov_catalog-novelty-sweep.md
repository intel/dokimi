<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog novelty ROOT`: the corpus novelty sweep under one budget.** Runs
  `skill novelty analyze` on every skill under ROOT, with `--max-usd` as the whole-run budget
  (required), `--max-usd-per-skill`, `--jobs`, and JSONL output with one row per skill as it
  finishes. `--resume` keeps the measured rows whose skill digest and settings are unchanged, and
  an existing `--jsonl` is not overwritten without it. `--strata CSV` adds bootstrap 95%
  intervals per stratum; `--summary-only` prints the summary of a JSONL with no model call. See
  `docs/skill-novelty.md`, Corpus sweep.
