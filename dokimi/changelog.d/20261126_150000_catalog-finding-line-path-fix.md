<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog check` findings carry `line` and `path`, and eight messages state the fix** (#942) — a finding row has an optional `line` (1-based, in `SKILL.md`, or in `path` when that is set) and an optional `path` (a bundled file, relative to the skill directory), each present only when the check knows it: the links and backticked paths of the body, the `name`, `description` and unknown frontmatter keys, and the bundled scripts. `cli_output.v1` gains an optional `path` and `catalog_eval.v1` an optional `line`. The messages of `name-mismatch`, `name-format`, `name-missing`, `description-too-long`, `script-crlf`, `link-missing`, `path-missing` and `frontmatter-unknown-key` now end with the edit that clears them, so match on `code`, not on the text.
