<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A subject scorecard digest no longer raises for a ref or question value longer than the file-name limit** — `ref_digest`, `distribution_digest`, `question_set_hash` and `baseline_digest` probed the text with `Path.is_file()` / `is_dir()`, which raise `OSError: [Errno 36] File name too long` on Linux for a name over 255 bytes. A text the OS refuses with that error (or its Windows equivalent) is now read as naming no file or directory, the same as a missing name: it digests with the tag `t`. The probe is now a `stat` of the path, since `is_file()` and `is_dir()` swallow every `OSError` on Python 3.14: any other `OSError` from it (a permission or I/O error on an existing path) propagates on every supported Python rather than giving a text-only digest; so does the stat of each file the walk under a directory ref lists, which on Python 3.14 used to digest a file in a directory that can be listed but not searched as no file. A subdirectory that cannot be listed is still left out of the digest without an error. The digest of an existing or a normal-length missing name is unchanged.
