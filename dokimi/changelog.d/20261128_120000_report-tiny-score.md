<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo report` no longer prints a score near 1e-4 as `score 0.00`** — a non-zero score that 2 decimals would print as `0.00` now prints at 2 significant digits, as the `repo compare` tables do: a mean of `1e-4` reads `score 0.0001` and `1e-12` reads `score 1e-12`. A score 2 decimals show as non-zero (`0.50`), an exact zero (`0.00`) and a missing score (`score -`) print as before.
