<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **Optional `version` and `provenance` on repository tasks (`task.json`), executable tasks (`metadata` in `task.toml`) and question-set items.** `provenance` is `{source, reviewed_by, status}`: `source` is `hand-written`, `generated:<tool>@<version>` or `imported:<format>`, and `status` is `candidate`, `validated` or `frozen`. Every key is optional and an absent one is `unknown`, never an error. `eval tasks generate` fills `source` as `generated:dokimi-tasks-generate@<dokimi version>`, and question generation fills `generated:dokimi-questions-persona@…` or, for chunk-grounded questions, `generated:dokimi-questions-chunk@…`. `repo report` shows each task's status in a `status` column (and in its JSON rows), and the executable task-run report shows it in a `Status` column. A malformed value fails `repo validate` for `task.json`, and is a warning for a `task.toml` and for a question-set item (the set still loads, and the value is kept as written). See `docs/repo-evaluation-contract.md` §3 and `docs/contributing-terminal-bench-task.md` §3 and §9.
