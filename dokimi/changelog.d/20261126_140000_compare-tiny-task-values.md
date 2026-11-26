<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo compare` no longer prints a per-task `baseline`, `treatment` or `delta`, or an arm mean, below 5e-7 as `0.0`** — a value that 6 places would put at 0 now keeps 2 significant digits and its sign, as `mde` and the interval ends already do: eight tasks with baseline `1e-12` print `1e-12`, not `0.0`, in `tasks` and `baseline_mean`. Values of 5e-7 and above print as before, and the keys and types are unchanged. A stored `repo compare` JSON differs from a new one on such values.
