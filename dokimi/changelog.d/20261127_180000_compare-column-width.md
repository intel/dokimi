<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A wide cell in the `repo compare` text table no longer pushes the rest of its row out of line** — each column was a fixed width (`delta [95% CI]` 26 characters, the arm means 9), so a delta with 2-significant-digit ends (`+3.4e-12 [+2e-12, +4.8e-12]`, 27) or a large metric mean (`1234567.500`, 11) shifted every cell after it. A column is now as wide as its widest cell in any table of the run, and never narrower than before, so a table whose cells fit prints as before. A negative zero now prints as `0.000`, not `-0.000`, in that table and the rule ledger.
