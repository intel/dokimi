<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The `changed:` line of `repo compare` no longer prints a per-task delta below 0.005 as `+0.00` or `-0.00`** — a listed task whose delta 2 decimals would print as zero now prints at 3 decimals, or at 2 significant digits when those read zero, with its sign: deltas of 1e-4, -1e-4 and 2.5e-4 read `+0.0001`, `-0.0001` and `+0.00025`. A delta 2 decimals show as non-zero (`+0.25`, `+1.00`) prints as before.
