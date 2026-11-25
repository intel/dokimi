<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A `.bzl` line that opens with `(` or `[` and ends in a long run of spaces is read in linear time** — the `repo scan` check for a form of binding it does not read (`(x, y) = ...`) split the run of spaces between `[^\n]*` and `\s*` at every position before it failed (`"(" + " " * 6000 + "x"` took 0.02 s, 15 times what 1500 spaces took). The check now looks for an `=` on the line, or after blank lines, so the time grows with the length. The same files report the form.
