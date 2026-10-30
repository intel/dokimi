<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- Task runs preserve artifacts and exit nonzero for any errored, unscored or missing planned cell, including partly errored runs that previously exited 0; supported dry runs remain plans and verifier failures remain scored outcomes.
- Undecided judge-validity checks visibly report INCONCLUSIVE, with the existing default and strict exit policies.

### Added

- Opt-in baseline context-score and paired-lift regression thresholds, requiring complete matched scoring and known matching model identities. Existing `--strict` remains an identity check.
