<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **The `listing-over-budget` warning names the five biggest entries (#970).** It now ends with
  `; largest: name (N chars), ...`, so the reader sees what to shorten; the three longest descriptions covered the
  excess in only half of the test cases. The same text is in the `catalog:listing` finding, and `listing.largest` in
  `--json` lists them when the total is over the cap.
