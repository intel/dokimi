<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Reps and concurrency flags have one spelling each (part of #94, F1b).** Repetitions of one cell are
  `--reps`: `tasks run` `--repeats`, `subjects run` `--work-repeats`, `benchmark run` `--multi-run` and
  `catalog trigger-probe` `--trials` are now `--reps`. How much runs at once is `--concurrency`: `catalog check`,
  `eval check` and `repo run` `--jobs` are now `--concurrency`, and `-j` stays on `eval check` and `repo run`.
  The old spellings exit 2 and print the new command. The values mean what they meant; the JSON output keys
  are unchanged.
