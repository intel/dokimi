<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A long run of spaces in seven more regexes is read in linear time** — two whitespace runs on either side of an optional token let the regex split a run in every possible way before it failed: the `trace prefilter` table delimiter check (`" " * 6000 + "|"` took 0.17 s, 16 times what 1500 spaces took), the `repo scan` assignment scan of a `.bzl` file (`"a" + " " * 6000 + "x"`, 0.015 s, 15 times), the memory-lint empty link `](   x`, the front matter key check (`" " * 6000 + "x"`, 0.18 s, 16 times), the `sed` print-only script check, the Inspect AI docstring section heading (`"Args" + " " * 6000 + "x"`, 0.055 s, 16 times) and the novelty `version_gate` tag (`"requires" + " " * 6000 + "!"`, 0.07 s, 16 times). Each whitespace run is now written once, so the time grows with the length. The same strings match, with the same groups and ends.
