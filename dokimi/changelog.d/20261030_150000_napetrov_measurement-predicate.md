<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A non-finite gate metric no longer passes its gate** — a repository task whose `gate` names a numeric metric (`{"metric": ..., "min": ...}`) counted a cell as a success when its `grade.json` held `NaN` or `Infinity` for that metric, since no comparison with NaN holds and an infinity clears any minimum. Such a cell is now a failure (`success: false`). `repo validate` now refuses a task whose `gate` minimum is `NaN` or infinite, and a `score.max` past `1e150` (the largest score `repo report` reads), which used to make `repo report` exit 1 on the whole batch. A `timeout_s`, `budget_usd` or `max_turns` written as an integer past the float range is reported as a problem instead of crashing the check, and so is a reference-check `weight` or `threshold` of that size. Cells recorded before this change keep the result they were recorded with; a re-run cell is graded again.
