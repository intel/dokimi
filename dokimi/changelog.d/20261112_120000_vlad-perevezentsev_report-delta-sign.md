<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`report generate` lists only improvements under "Biggest Improvements" and only degradations under "Biggest Degradations"** —
  both tables took the ten largest deltas whatever their sign, so a run with fewer than ten
  improved questions showed its negative deltas there, with a fixed `+` in front of the minus
  (`+-2.5`). A delta of exactly 0 is in neither table; one that rounds to 0 is listed by its
  sign and prints `+0.0` or `-0.0`. The `top_deltas` and `bottom_deltas` keys of
  `report generate --format json` are filtered the same way.
