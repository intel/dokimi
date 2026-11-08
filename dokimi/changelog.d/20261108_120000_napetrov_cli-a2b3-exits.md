<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **Input refusals exit 2 with the same `Error: <message>` line** — the refusals that still printed a `✗`, `Error building arms/plugins`, `Error loading matrix run inputs`, `Error computing plugin delta` or a bare message now go through one path: in `arms`, `baseline`, `benchmark`, `eval coverage`, `repo`, `report`, `run`, `tools` and the `eval` commands that read questions or judge sheets. The arms messages read `Error: cannot build arms/plugins: ...`, `cannot load matrix run inputs: ...` and `cannot compute plugin delta: ...`.
- **Runtime failures and failed checks exit 1, not 2** — `repo hits` on a batch another `repo run` holds, `eval coverage` when the evals' `needs` labels have problems (a failed check), and `tools preference` when every call failed.
- **`tools preference` exits 3 when a budget stops the run** — `--max-cost-usd` reached, or a call with no price, keeps the partial artifact and exits 3 (`EXIT_INCONCLUSIVE`, the run-incomplete code in `docs/quickstart.md`) instead of 2.
