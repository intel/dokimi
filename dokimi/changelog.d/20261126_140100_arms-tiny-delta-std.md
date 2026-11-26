<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **An arms row no longer prints a `delta_std` below 0.005 as `0.0`** — a spread that 2 places would put at 0 now keeps 2 significant digits, as `delta_mean` does: deltas of 2e-12 to 9e-12 print `delta_std` `2.4e-12` beside `delta_mean` `4.5e-12`, not `0.0`. A spread of 0.005 and above, and an exact 0, print as before. A stored arms result differs from a new one on rows with such a spread.
