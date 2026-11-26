<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo security` reads a line of repeated `git push`, `git reset` or `-a/git` options in linear time** (#1212, #1213) — the `history-rewrite` rule rescanned the rest of its segment for a force flag or `--hard` from every `git push` and `git reset`, and a chain `git -a/git -a/git ...` was walked to its end from every `git` in the commit branch of `hook-bypass`, in `history-rewrite` and in the push branch, so `"git push " * 6000` and `"git " + "-a/git " * 6000` took time quadratic in the repeats. `history-rewrite` now starts from the first `git push` (and the first `git reset`) of each segment, as the commit branch of `hook-bypass` does, and an option word that ends in `git` is read from that `git`, as the argument of `-C` already is: the findings are the same.
