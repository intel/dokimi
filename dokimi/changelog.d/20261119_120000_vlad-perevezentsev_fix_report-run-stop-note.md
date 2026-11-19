<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A report of a run that stopped early says so at its top.** When `arms run` or `answers generate`
  stops at a run-wide error (a usage limit, a missing login), the arms .md and the `report eval` page
  (one-condition and paired) open with `Stopped early after N of M questions: <reason>`, and say that
  every number below covers those N questions only; before, a shortened run read as a complete one.
  `eval score reference`, `pointwise` and `panel` copy the answers file's `terminated` block into the
  eval's `run_metadata`, so the page of an eval judged from a stopped run shows it too (#895).
