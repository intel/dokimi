<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Four long single-line inputs are read in linear time instead of taking seconds to minutes** — a codex review answer with a long run of `[P1]` tags or of path-like characters such as `a.a.a.` (6000 tags took 0.35 s, 800 `a.` repeats took 0.8 s), a reply made of a code fence and thousands of blank lines (6000 took 34 s), a C header with thousands of unclosed `/*` (6000 took 0.22 s), and a README command line with a long whitespace run (6000 spaces took 17 ms, 15 times what 1500 took). The results are unchanged: the same findings, the same unwrapped JSON body, the same names read from the header and the same command line.
