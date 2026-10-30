<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`dokimi runs list` lists the artifacts under the runs root** — the root is
  `results/` in the working directory, or `DOKIMI_RUNS_DIR`. One row per artifact:
  its `schema_version`, subject, model, recorded time and path, newest first;
  `--kind` (repeatable) and `--subject` (an exact match) filter the rows, and
  `--json` prints them with the fields `schema_version`, `kind`, `run_id` (always
  `null` until artifacts carry one), `subject`, `model`, `generated_at`, `path`
  and `size_bytes`. A file is an artifact when it opens with the
  `schema_version` of a registered kind; every other file is counted by why it
  was skipped, and a file that cannot be read is listed with its reason. A
  missing `results/` prints "no runs" and exits 0; a `DOKIMI_RUNS_DIR` that does
  not exist or is not a directory exits 2. The command only reads.
