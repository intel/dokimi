<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **The reach matrix's `fixtures_digest` raises when a directory of the fixtures cannot be listed** — it walked with `Path.rglob`, which skips a directory it cannot list (mode `0o111`) without an error, so the matrix carried the digest of a smaller fixture tree. It now raises `PermissionError` naming the directory. The digest of a tree that can be listed is unchanged, and a link (to a directory too, or dangling) is still hashed by its target.
