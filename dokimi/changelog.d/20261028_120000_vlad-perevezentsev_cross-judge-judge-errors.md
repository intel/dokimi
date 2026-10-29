<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`eval cross-judge` says why a `--judges` judge left answers unscored** — a
  failed judge call used to drop the answer and its error, so a judge with no
  API key, an unknown model or an unreachable endpoint gave `NOT MEASURED` and
  exit 0 with no reason anywhere. Each measure with unscored answers now logs
  one warning, `<judge> (<kind>): N of M answers unscored, first error: ...`,
  and the report keeps the same counts under `unscored` (JSON) and as a note
  under the table (Markdown). A judge that scored none of its answers is a
  failed run: after the report is written, `cross-judge` prints an `Error:`
  line and exits 2, with or without `--strict`, as `eval pairwise` does when
  every comparison fails. A judge that scored some answers keeps exit 0.
