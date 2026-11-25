<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A sign-flip row of tiny-scale deltas no longer prints its interval ends, mean and `mde` as `0.0001` / `0.0`** — an end or `delta.mean` that 4 places would put at 0 (below 5e-5), and an `mde` that 6 places would put at 0, now keeps 2 significant digits and its side of 0, as `delta_mean` of the arms track already did: deltas of 2e-12 to 9e-12 print the interval `[2.3e-12, 6.7e-12]` around the mean `4.5e-12`, not a point at `0.0001`. Ends, means and `mde` that round to a non-zero number print as before; `exact` output is unchanged. A stored `repo compare` JSON or arms result differs from a new one on rows below 5e-5.
