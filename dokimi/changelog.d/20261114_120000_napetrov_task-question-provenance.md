<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **Optional `version` and `provenance` on repository tasks (`task.json`), executable tasks (`metadata` in `task.toml`) and question-set items.** `provenance` is `{source, reviewed_by, status}`: `source` is `hand-written`, `generated:<tool>@<version>` or `imported:<format>`, and `status` is `candidate`, `validated` or `frozen`. Every key is optional and an absent one is `unknown`, never an error. `eval tasks generate` and `eval questions generate` fill `source` as `generated:dokimi-<command>@<dokimi version>`. `repo report` shows each task's status in a `status` column (and in its JSON rows), and the executable task-run report shows it in a `Status` column. A malformed value fails `repo check` for `task.json`, is refused by the `questions.v2` schema, and is a warning for a `task.toml`. See `docs/repo-evaluation-contract.md` §3 and `docs/contributing-terminal-bench-task.md` §3 and §9.
