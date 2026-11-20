<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A report of a run that stopped early says so at its top.** When `arms run` or `answers generate`
  stops at a run-wide error (a usage limit, a missing login), the arms .md and the `report eval` page
  (one-condition and paired) open with `Stopped early: <reason>. N of M questions have a record`
  (a question the stop interrupted counts among the N), and say that every number below covers only
  questions answered before the stop; before, a shortened run read as a complete one.
  `eval score reference`, `pointwise` and `panel` copy the answers file's `terminated` block into the
  eval's `run_metadata`, so the page of an eval judged from a stopped run shows it too (#895).
