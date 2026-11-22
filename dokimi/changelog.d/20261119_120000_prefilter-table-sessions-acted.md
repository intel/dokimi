<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`trace prefilter` prints `sessions_acted` in its text table (#937)** — a rule that was `in_use` because the agent ran its command in most sessions showed `0/5` triggered sessions and `0/0` acted, with no count that explained the status. The table has an `acted.sess` column after `trig.sess`, the same count the `--json` row carries. The status column is now as wide as `candidate_for_ablation`, so the counts of those rows sit under their headers.
