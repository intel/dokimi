<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`dokimi repo tasks replay` builds `dokimi:review_replay` tasks from past
  reviews** — given a threads file `gh api graphql` wrote (the query is in
  contract §4.6), it writes one task per merged pull request: `change.patch`
  (the change at the revision most threads were written on, from its merge
  base with the base branch), `task.json`, and `gold.json` from the threads a
  reviewer opened on a line of the new file. `addressed` is true when the PR's
  own later commits changed the file within `--window` lines of the thread, so
  neither a merge of the base branch into the PR nor a bot's commit counts. Files the guidance
  globs match and files under `eval_dir` are left out of the patch and the
  gold, so the task applies under every guidance arm. Every thread, review body
  and pull request left out is printed with the reason; a task directory that
  exists is kept unless `--force`. It makes no GitHub call. It exits 1 when no
  pull request gave a task, and on an error that is not one pull request's own
  (the threads file, the source, the manifest, the prompt file, writing a
  task); a git failure while one pull request is built skips that one.
