<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`catalog eval --out-dir` keeps the group and mode of the receipts a rerun replaces** — `catalog.json`, `plan.json`, `result.json` and `report.md` were staged as new files and renamed over the previous ones, so a `0o640` receipt in a supplementary group came back `0o664` in the user's primary group (the cause of #1067 and #1165). The staged file now gets the previous receipt's mode and, where the user may give it, its group (root also the owner) before the rename, through `dokimi.atomic_write.keep_owner_group_mode`. A user who may not give the group keeps the receipt published in their own group, without an error; a receipt with no previous file keeps the umask mode as before. An access ACL the staged file inherited from the output directory's default ACL is dropped before the mode is set, so the receipt grants no named user more than the one it replaces. The previous receipts are still moved aside first and put back as they were when the swap fails, and a new receipt that took a read-only mode is made writable before that rollback removes it, as Windows refuses to unlink a read-only file.
