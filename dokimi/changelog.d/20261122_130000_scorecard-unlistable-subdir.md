<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A scorecard's directory-ref digest raises when a subdirectory cannot be listed** — the walk under a directory ref used `Path.rglob`, which skips a subdirectory it cannot list (mode `0o111`) without an error, so the scorecard recorded a digest of a smaller tree. The walk is now `os.walk` with an `onerror` that re-raises, so `ref_digest` and the arm-spec digest raise `PermissionError` naming the directory. The digest of a tree that can be listed is unchanged, and a symlink to a directory is still not followed.
