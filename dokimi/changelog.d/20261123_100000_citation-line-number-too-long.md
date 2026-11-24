<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **An explorer answer whose citation line number has more than 4300 digits no longer stops scoring with a `ValueError`** — `parse_final_answer` passed the digits to `int()`, which refuses more than 4300 of them. Leading zeros do not count, as in the Codex review converter. In a `<final_answer>` block the line now counts as malformed (it lowers the validity rate), and in loose text the token is skipped.
