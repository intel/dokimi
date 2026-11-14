<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Exit codes 3, 4 and 5 for the cases below** — scripts that branch on the exit code see these changes:
  - `dokimi eval judge validate` on an inconclusive result exits 3 (inconclusive) instead of 0; `--strict` still exits 1.
  - `dokimi baseline compare --strict`, and `baseline compare` with the regression gate (`--max-context-drop` or `--max-lift-drop`), exit 4 (invalid) when the model differs or cannot be verified; they exited 1 before. A regression verdict still exits 1.
  - `dokimi eval leak` and `dokimi eval redteam` exit 3 (inconclusive) when the result is inconclusive; they exited 1 before. A blocked run still exits 1.
  - An uncaught exception (a crash) exits 5 (infra failure) in every command, and prints the traceback; it exited 1 before. For a command that emits the cli_output document, `--json` also prints an `infra` error document.
