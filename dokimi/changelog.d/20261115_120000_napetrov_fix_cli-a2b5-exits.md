<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Exit codes for the cases below** — scripts that branch on the exit code see these changes:
  - `dokimi repo run`: a cell that crashes (a harness failure, a docker daemon or CLI failure, a scratch directory that vanished, a result that cannot be written) exits 5 (infra failure); it exited 1 before. A cell refused on its inputs (for example a malformed `.mcp.json`), a budget stop or a failed host probe still exits 1.
  - `dokimi eval judge agreement --strict` exits 3 (inconclusive) when an arm is NOT MEASURED or no arm can be compared, and 4 (invalid) when an arm is JUDGE-DEPENDENT; it exited 1 for both before. Without `--strict` the exit code is unchanged (0), and a judge that scored none of its answers still exits 1.
  - `dokimi trace inspect`, `dokimi trace consistency` exit 2 (bad input) when no trace can be read; they exited 1 before. With `--json` they print the `input` error document (`error.code` `input`, `data` null) instead of a document with `status` `failed`. A partial read still exits 1.
