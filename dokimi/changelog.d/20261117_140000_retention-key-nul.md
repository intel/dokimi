<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A novelty retention cache entry is no longer replayed for a different atom when a NUL separates them** — the key joined the statement and the text with a bare NUL, so a statement ending in NUL and a text starting with NUL hashed the same bytes. The key now hashes the statement's length before the statement. The cache version moves to `retention-cache-v6`, so entries already in `--cache-dir` are not read; the old files can be deleted, and the first run after upgrading pays the adjudication calls again.
