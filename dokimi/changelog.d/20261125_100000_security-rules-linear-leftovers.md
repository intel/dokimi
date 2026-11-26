<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo security` reads a word of repeated `-v`, a line of repeated `git commit` and a chain of `git -C` in linear time** (#1160, #1161, #1175) — the `container-escape` rule scanned the rest of a word for `docker.sock` after every `-v`, the commit branch of `hook-bypass` scanned the rest of its segment after every `git commit`, and the commit branch of `hook-bypass` and `history-rewrite` walked the rest of a chain `git -C git -C git ...` from every `git`, so `"docker run " + "-v" * 6000`, `"git commit " * 6000` and `"git -C " * 6000` took time quadratic in the repeats. The word scan stops at the next `-v`, `--volume` or `--mount`, the commit branch starts from the first `git commit` of each segment, and the argument of `-C` is never a word that ends in `git`, as in the push branch: the findings are the same.
