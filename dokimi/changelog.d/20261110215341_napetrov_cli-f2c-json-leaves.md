<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`--json` of ten more leaves is `cli_output.v1` (shape change).** `catalog formats`, `eval inspect`,
  `repo validate|scan|inspect|fetch|budget|report|compare` and `repo hits judge` now print the envelope on
  stdout, with the payload they printed before under `data`. Read `data.<key>` where you read `<key>` today;
  `status` is `complete`; `failed` when the leaf exits 1, `budget_stopped` when `repo hits judge` stops at a cap.
  Exit codes of the documented paths are unchanged. Two exits differ: an uncaught crash now prints the document
  and exits 5 (`error.code` `infra`), where it used to exit 1 with a traceback; a usage error (a bad value, a
  missing or unknown argument) prints a `failed` document and exits 2 (`input`). A leaf that exits without its
  document prints one (`status: failed`) with the same exit code.
  `repo hits judge` fills the envelope `cost`. `.github/workflows/repo-eval.yml` reads the new shape.
- **`--json` error paths.** A moved command or flag under `--json` prints a `failed` document (`error.code`
  `moved`, exit 2) with the hint in `reason`. A failed `repo validate|scan|inspect|budget` gate says why in
  `reason`. A crash in `repo hits judge` reports the cost of the calls it made. `emit` no longer stringifies a
  value JSON cannot hold; `eval inspect` still does.
