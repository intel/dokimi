<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`catalog check --listing-budget` names what to shorten (#970).** The `listing-over-budget` warning now ends with
  `largest entries: name (chars), ...`: the fewest largest entries that cover the excess, at most five. They are also
  `listing.largest` in the JSON report, with `excess`, `covered`, `top3_covered`, `top5_covered` and their shares: how much of the excess the named, the three and the five largest entries account for. The total, the cap and the exit codes are unchanged.
