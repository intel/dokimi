<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A reply with no `<final_answer>` block is scanned for citations in linear time** — `parse_final_answer` read a long run of path characters with no `.ext:digits` quadratically: `"a" * 6000` took 0.041 s, 16 times what 1500 took, and `"<final_answer>" * 6000`, which has no block, took 8.6 s, 16 times what 1500 took. It now reads each run once and finds the citations the old pattern found, with the same paths, ranges and wrapping brackets.
