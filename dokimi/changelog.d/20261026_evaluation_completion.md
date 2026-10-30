<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- Task runs preserve artifacts and exit nonzero for any errored, unscored or missing planned cell, including partly errored runs that previously exited 0; supported dry runs remain plans and verifier failures remain scored outcomes.

### Fixed

- `baseline compare` now treats a blank or case-variant "Unknown" provider or model, and a panel member without a `provider/` prefix, as not recorded, for every run: the "not compared" warning appears and `--strict` exits 1 where it used to pass.
- A command harness without a reward file records its exit code as reward 1.0 or 0.0, so `tasks run` counts it as scored.
- Undecided judge-validity checks visibly report INCONCLUSIVE, with the existing default and strict exit policies.

### Added

- Opt-in baseline context-score and paired-lift regression thresholds, requiring complete matched scoring and known matching model identities. Existing `--strict` remains an identity check.
