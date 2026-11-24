<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`catalog scan --resume` rescans a skill whose digest is null** — a prior row with `skill_sha256: null` was kept when the skill's digest is null now too (`None == None`), so a skill with an unlistable subdirectory kept its old findings after `SKILL.md` changed. It is rescanned.
