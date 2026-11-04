<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **A reach conformance suite and a nightly agent × version matrix** — `data/reach-conformance/` holds eight fixture repositories (root, nested, `CLAUDE.md` with `AGENTS.md`, a Claude Code `@` import, an `AGENTS.override.md`, a subdirectory shadow, a file over the size cap, a symbolic link), and `expectations.yaml` (`reach_conformance.v1`) gives what each harness's vendor documentation says each file loads in each scenario, or `unknown`. `scripts/reach_conformance.py` checks the expectations against the fixtures, runs `repo reach --format receipt` over them for each harness with a model, credential and CLI present (the rest are recorded as skipped), and merges the per-harness `reach_matrix.v1` JSON into one matrix and one markdown page. `.github/workflows/reach-matrix.yml` runs it nightly and on dispatch, not as a pull-request check, and uploads the matrix as an artifact. The design, the agreement rules and the limits are in `docs/reach-matrix.md`.
