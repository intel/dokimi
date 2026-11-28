<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The text tables of `repo compare` and the rule ledger no longer print a tiny arm mean or delta as `0.000`** — a non-zero arm mean or delta mean that 3 decimals would print as `0.000` now prints at 2 significant digits with its sign, as the interval beside it does: eight tasks near 1e-12 read `1e-12  4.4e-12  +3.4e-12 [+2e-12, +4.8e-12]`, and the ledger `mean score 1e-12 -> 4.4e-12, delta +3.4e-12 [+2e-12, +4.8e-12]`. A value 3 decimals show as non-zero, an exact zero, and a missing value (`-`) print as before.
