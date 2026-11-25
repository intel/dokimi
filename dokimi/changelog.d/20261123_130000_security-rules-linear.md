<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo security` reads a long line of `docker run` or `git push` in linear time** — the `container-escape` rule and the `push --no-verify` branch of `hook-bypass` read a line of many openers and no flag quadratically (`"docker run " * 6000` took 2.4 s, 16 times what 1500 took). Each now starts from the first opener of a line only, with the same findings.
