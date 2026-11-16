<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`novelty`: a run stopped for a missing model price is recorded as `no_price`, not `spend_cap`.**
  A partial novelty arm (`partial` in the output) used to name `spend_cap` whatever stopped the
  run. It now carries the stop reason the router raised. Scripts that matched `spend_cap` on such
  records should also match `no_price`.
- **`catalog eval --tier static`: any failure after the output directory is accepted replaces the
  earlier receipts with an error report.** Before, only input errors did, so a bug left the
  previous PASS on disk. The run still exits non-zero. An interrupt (Ctrl-C) still keeps the
  previous receipts.
