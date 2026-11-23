<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Markdown code spans are found in linear time on backtick runs of different lengths with no closer** — the code-span readers of `catalog check` (links, commands, URLs in code), `memory lint` and the answer degrader (`eval`) scanned to the end of the text for every run of backticks that had no run of the same length after it. A text of runs of 1, 2, 3, ... backticks, each followed by `a`, took 0.15 s at 400 runs (80600 characters), 60 times what 100 runs (5150 characters) took, and the URL reader of `catalog check --online` 0.42 s, 71 times. All four now read each run once and match it with the next run of the same length: 0.3 ms at 400 runs, 3 times what 100 runs take. The spans read are unchanged.
