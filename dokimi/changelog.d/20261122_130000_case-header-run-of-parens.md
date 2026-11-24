<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A Bash call with a run of `(` before `case` no longer takes exponential time to read** — `split_commands("(" * n + "case")` took 0.010 s at n=18, 0.164 s at n=22 and 0.649 s at n=24, doubling with each added `(`, and n=1000 did not finish; the `case ... in` header pattern split a run of `(` into groups in every possible way before it gave up on a `case` with no `in`. It now takes each `(` as one group, so a run splits one way, and the commands read from a line are unchanged.
