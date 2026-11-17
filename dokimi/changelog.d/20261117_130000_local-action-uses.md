<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A local Action a workflow uses is no longer an `unresolved` path claim.** `repo scan` kept a `uses:` value only when it had an `@ref`, so a claim `./source/.github/actions/setup-miniconda` stayed `unresolved` even when a workflow step used exactly that string. A local `uses: ./<path>` (no ref) in a workflow or composite action now makes the claim `ok` with `used by <file>`; a path no step uses, a typo of one, a name with an `@ref`, and a path the tree holds or a submodule holds keep their verdicts (#857).
