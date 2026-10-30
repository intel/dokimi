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
- **`dokimi runs show PATH` shows one artifact** — `PATH` as `runs list` prints
  it (relative to the root), or an absolute or working-directory path inside the
  root once symlinks are resolved (neither command reads a file outside the
  root). It prints the artifact's kind, its fields, the length of the lists it
  holds (answers, evaluations, cells, ...) and the files next to it, and checks
  it against its schema: an artifact that fails the check is shown with
  "schema: invalid, N errors" and every error, and the command still exits 0.
  A path outside the root, a missing file, a directory, or a file that is not a
  readable artifact of a registered kind exits 2. `--json` prints the same
  fields as a `runs list` row plus `schema`, `schema_valid`, `schema_errors`,
  `contents` and the neighbours.
