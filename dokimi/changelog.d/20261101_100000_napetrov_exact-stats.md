<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **Unrounded statistics next to the rounded ones** — every paired significance block (`summary.per_arm[arm].significance` in `arms.v1`, and the cells that copy it) gains `exact: {delta_mean, ci_lo, ci_hi, p_adjusted}`: the values before rounding. `delta_mean` (2 decimals), `ci_lo`/`ci_hi` (4) and `p_adjusted` (4) are unchanged, so existing readers see the same numbers; a consumer that publishes the effect or re-derives a verdict reads `exact`.
