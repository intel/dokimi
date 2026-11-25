<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo security` reads a word of repeated `-v` and a line of repeated `git commit` in linear time** (#1160, #1161) — the `container-escape` rule scanned the rest of a word for `docker.sock` after every `-v`, and the commit branch of `hook-bypass` scanned the rest of its segment after every `git commit`, so `"docker run " + "-v" * 6000` and `"git commit " * 6000` took time quadratic in the repeats. The word scan now stops at the next `-v`, `--volume` or `--mount`, and the commit branch starts from the first `git commit` of each segment: the findings are the same.
