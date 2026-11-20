<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **Subject scorecard digests no longer share a value across different file sets** — `ref_digest`, `question_set_hash` and `baseline_digest` hashed each file's raw bytes next to NUL-separated names, so a file holding `b\0c\0` hashed like two files `a` and `c`. They now hash the name and the content of each file as separate fixed-width digests, and `question_set_hash` tags each question as naming a file or not, so a question file whose content spells the next question no longer hashes like the two questions. A `ref_digest` of a single file stays the sha256 of its content, and that of a directory is now the same on Windows as on Linux for the same tree. Digests of scorecards written by earlier releases differ from a fresh one for the same inputs, except that of a single file; nothing in dokimi compares the two, so rebuild a scorecard to get the new value, and regenerate the `subjects evidence` documents made from it (`content_digest`, `eval_set.digest`).
