<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`report generate` prints a negative delta in "Biggest Improvements" as `-2.5`, not `+-2.5`** —
  the table lists the ten largest deltas whatever their sign, so a run with fewer than ten
  improved questions showed its other deltas with a fixed `+` in front of the minus. "Biggest
  Degradations" now prints a positive delta with its `+` too (`+0.0`, was `0.0`).
