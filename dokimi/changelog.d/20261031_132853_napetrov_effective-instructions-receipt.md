<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **An effective-instructions receipt for a reach probe** — `repo reach --format receipt` prints, and writes to `effective_instructions.json` in `--out`, the receipt of `effective-instructions/v0` (agentsmd/agents.md#213) for each probed scenario: each guidance source's path, sha256, scope, expected state (from `repo budget`'s start-up rules, Claude Code only), observed state (`loaded`, `fetched`, `not_loaded` or `unknown`), `observation_method` (`unknown`) with dokimi's own `dokimi_method` (`marker_self_report`: the model reports the marker tokens it sees; the prompt is not captured), the harness's name and version, the displacements seen in the run, and the unknowns. `repo reach --from <reach.json> --format receipt` builds the same receipt from a saved probe without a harness run, after checking the file's repository, harness and commit against the source; both must be at a commit (a working tree is a usage error, exit 2). `followed` is always `unknown`: the probe does not show what the model did with a loaded source. The receipt makes no LLM call and is validated against `schemas/effective_instructions.v1.json`. The mapping and its limits are in `docs/repo-evaluation-contract.md` §7.1. The default output of `repo reach` is unchanged.
