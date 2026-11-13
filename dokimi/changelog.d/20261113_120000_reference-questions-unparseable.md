<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`eval score reference` refuses an unparseable `--questions` file** — an empty, non-JSON or non-UTF-8 file now exits 2 with `Error: --questions <path>: <reason>`, like a missing path, instead of ending in a traceback with exit 5.
