<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A Codex review header whose line number has more than 9 digits no longer stops the answer conversion with a `ValueError`** — `- [P1] title — /path/a.py:<digits>` is no finding then: it counts as an unparsed marker, so `convert_answer` appends no findings block, as for any other header it cannot parse.
