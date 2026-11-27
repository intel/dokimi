<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The inconclusive note of `repo compare` no longer prints a tiny `mde` as `±0.000`** — a non-zero `mde` below 0.0005 now prints in scientific notation at 2 significant digits, as the row's `mde` field carries it: a row of deltas around 2e-12 reads `about ±2.6e-12 (8 tasks)`. An `mde` of 0.0005 and above keeps its 3 decimals, and from 1e6 its `.3e` form.
