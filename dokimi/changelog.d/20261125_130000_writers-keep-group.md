<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A doc-cache entry, a batch spend ledger and `skill novelty ground` keep the group and mode of the file they rewrite** — each replaced the file by a new inode in the user's primary group: a `0o640` cache entry came back `0o664` and the ledger `0o600`, and the grounded atoms file lost its group. They now publish through `dokimi.atomic_write.write_text_atomic`, as `save_artifact` does. A link a grader left at the ledger's path is still replaced, never followed; the failure forms and the bytes written are unchanged.
