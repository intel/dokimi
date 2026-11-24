<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog novelty ROOT`: the corpus novelty sweep under one budget.** Runs
  `skill novelty analyze` on every skill under ROOT, with `--max-usd` as the whole-run budget
  (required), `--max-usd-per-skill`, `--jobs`, and JSONL output with one row per skill as it
  finishes. `--resume` keeps the measured rows whose skill digest and settings are unchanged, and
  a `--resume` under other settings is refused unless `--rerun-changed`, and an existing `--jsonl` is not overwritten without it. `--strata CSV` adds bootstrap 95%
  intervals per stratum; `--summary-only` prints the summary of a JSONL with no model call.
  Ctrl-C or SIGTERM cancels the skills not yet started, lets the running ones finish and keeps the
  rows done; a run that leaves a skill partial, error or not run exits 1 after writing its rows. A second signal kills the subprocess calls in flight and starts no further call. Identical
  skill directories (SKILL.md and bundled files) of one skill count once.
  The summary counts a renamed bundle once, keeps distinct coexisting skills, and retains the spend of every row.
  See `docs/skill-novelty.md`, Corpus sweep.
