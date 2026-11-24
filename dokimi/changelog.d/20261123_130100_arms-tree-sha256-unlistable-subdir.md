<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The skill digests of a repo arms run raise when a subdirectory cannot be listed** — `tree_sha256` (the skill pins of a batch, the skill records of a run) walked with `Path.rglob`, which skips a subdirectory it cannot list (mode `0o111`) without an error, so it digested a smaller tree. It now raises `PermissionError` naming the directory. The digest of a tree that can be listed is unchanged.
