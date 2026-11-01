<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- Task runs preserve artifacts and exit nonzero for any errored, unscored or missing planned cell, including partly errored runs that previously exited 0; supported dry runs remain plans and verifier failures remain scored outcomes.

### Fixed

- `baseline compare` now treats a blank, non-string or case-variant "Unknown" provider or model, and a panel member without a `provider/` prefix, as not recorded, for every run: the "not compared" warning appears and `--strict` exits 1 where it used to pass.
- A command harness with no reward file records its exit code as reward 1.0 or 0.0, so `tasks run` counts it as scored; exit 126, 127, signal exits and a reward file with no reward in it are no verdict, even with a `reward: x` on the output: the cell is errored and stays out of `pass_rate` and the paired test. `avg_reward` of command harnesses now includes these exit-code cells, so it does not compare with older `task_runs` artifacts.
- `tasks run` exits 2 before running when a task or harness is listed twice, including one task under two spellings and two tasks with the same directory name.
- Undecided judge-validity checks visibly report INCONCLUSIVE, with the existing default and strict exit policies.

### Added

- Opt-in baseline context-score and paired-lift regression thresholds, requiring complete matched scoring and known matching model identities. A regression and an identity mismatch exit 1; partial or insufficient evidence exits 2. `--min-matched` without a threshold exits 2. Existing `--strict` remains an identity check.
