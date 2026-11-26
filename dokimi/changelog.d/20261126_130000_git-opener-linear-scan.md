<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo security` reads a line of repeated `git push` or `git reset` in linear time** (#1212) — the `history-rewrite` rule rescanned the rest of its segment for a force flag or `--hard` from every `git push` and `git reset`, so `"git push " * 6000` took time quadratic in the repeats. It now starts from the first `git push` (and the first `git reset`) of each segment, as the commit branch of `hook-bypass` does: the findings are the same.
