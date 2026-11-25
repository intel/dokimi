<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo security` reads a word of repeated `-v` in linear time** (#1160) — the `container-escape` rule scanned the rest of a word for `docker.sock` after every `-v`, so `"docker run " + "-v" * 6000` took time quadratic in the repeats. The scan now stops at the next `-v`, `--volume` or `--mount`, which finds the same `docker.sock`: the findings are the same.
