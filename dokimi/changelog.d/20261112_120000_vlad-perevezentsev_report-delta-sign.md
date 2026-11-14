<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`report generate` lists only improvements under "Biggest Improvements" and only degradations under "Biggest Degradations"** —
  both tables took the ten largest deltas whatever their sign, so a run with fewer than ten
  improved questions showed its negative deltas there, with a fixed `+` in front of the minus
  (`+-2.5`). A zero delta is in neither table.
