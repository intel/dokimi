<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`dokimi runs list` groups the artifacts by run**: a new first column, `RUN`, is the
  artifact's `run_id` (`-` when it has none), and the artifacts of one run are listed together.
  The runs are newest first, each in the place of its newest artifact; an artifact with no
  `run_id` stands on its own, in its place by time. The summary has a new line that counts the
  runs and the artifacts with no `run_id`. `--json` lists `artifacts` in the same order and adds
  `runs`: each run's `run_id`, its `kinds` and the `paths` of its artifacts. `--kind` and
  `--subject` filter the rows before they are grouped.
- **`dokimi runs show RUN_ID` shows one run**: the artifacts that carry that `run_id`, as
  `runs list` rows, and the files under the root that could not be read, since one may belong
  to the run. An artifact file of that name still wins (under the root, or from the working
  directory and inside the root); a directory of that name, such as the run's own, does not. A
  run id that no artifact has exits 2, as a missing file did; so a missing file whose name could
  be a run id (`missing.json`) now says "no file missing.json under the runs root results/, and
  no artifact there has that run_id".
