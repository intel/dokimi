<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo budget` no longer compares every pair of skills for near-duplicate descriptions** — equal word sets are grouped and the rest are found through a prefix filter and a size bound, and skill names are counted once, so 4000 skills take about 0.02 s instead of 3.4 s. The `duplicate_skills` list, its order and its values are the same.
