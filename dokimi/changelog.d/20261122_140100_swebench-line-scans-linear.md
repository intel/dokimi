<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **SWE-bench sympy log grading and test-directive listing read a long line in linear time** — `parse_sympy` read a line of many spaces and no `.py:` quadratically (`"a " * 6000` took 0.12 s, 16 times what 1500 took) and `test_directives` read a line of repeated `diff --git a/` with no ` b/` the same way (6000 repeats took 0.19 s, 16 times what 1500 took). Both now read each line once, with the same results.
