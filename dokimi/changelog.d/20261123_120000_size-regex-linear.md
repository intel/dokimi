<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A compose memory limit followed by a long run of spaces is read in linear time** — the size check of a terminal-bench v1 task's `deploy.resources.limits.memory` split a run of spaces between its two whitespace runs at every position before it failed (`"1" + " " * 6000 + "x"` took 0.07 s, 16 times what 1500 spaces took). The first run is now possessive, so the time grows with the length. The same values are accepted, with the same number and unit.
