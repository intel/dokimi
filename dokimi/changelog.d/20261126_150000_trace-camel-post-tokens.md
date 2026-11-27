<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`trace inspect --json --calls` reports `post_tokens` for camelCase compact metadata** — a `compact_boundary` with `compactMetadata.postTokens` set to `8123` now gives `post_tokens` 8123 in `compaction_events`, not `null`, the way `preTokens` already gave `pre_tokens`.
