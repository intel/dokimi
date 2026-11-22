<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Text with many openers and no closer is read in linear time** — a session log prompt (`<system-reminder>`), a captured request (`<system-reminder>`, `<total_tokens>`), an explorer answer (`<final_answer>`), a Codex or Copilot trace command (`<<A` heredocs, `2>file` before `>&2`), a shell line (`<<A`), a review answer (a ```` ```json ```` fence), a SWE-bench log line (`[`) and a Claude JSON output (`{`) were scanned with a lazy or greedy regex that rescanned the rest of the text from every opener (6000 openers took 0.4 s to 1.2 s, and 800 `[` took 0.35 s with the cost growing cubically). Each now finds the closers once, so an opener with no closer costs no scan, and openers of other tags keep their own closers. The result for text that has a closer is unchanged.
