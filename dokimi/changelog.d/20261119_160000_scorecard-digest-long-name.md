<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A subject scorecard digest no longer raises for a ref or question value longer than the file-name limit** — `ref_digest`, `distribution_digest`, `question_set_hash` and `baseline_digest` probed the text with `Path.is_file()` / `is_dir()`, which raise `OSError: [Errno 36] File name too long` on Linux for a name over 255 bytes. A text the OS refuses with that error (or its Windows equivalent) is now read as naming no file or directory, the same as a missing name: it digests with the tag `t`. Any other `OSError` from the probe (a permission or I/O error on an existing path) still propagates rather than giving a text-only digest. The digest of an existing or a normal-length missing name is unchanged.
