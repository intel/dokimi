<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A catalog scan row has no `skill_sha256` when a subdirectory of the skill cannot be listed** — `skill_dir_digest` walked with `Path.rglob`, which skips a subdirectory it cannot list (mode `0o111`) without an error, so the row carried a digest of a smaller tree. The walk is now strict: the digest is `null`, and `--resume` reviews the skill again. The digest of a tree that can be listed is unchanged.
