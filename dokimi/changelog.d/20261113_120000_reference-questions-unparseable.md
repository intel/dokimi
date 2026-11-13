<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`eval score reference` refuses an unusable `--questions` file** — a missing, empty, non-JSON or non-UTF-8 file, a directory, or JSON that is not a list of questions now exits 2 with `Error: --questions <path>: <reason>` (or the validation message), instead of ending in a traceback with exit 5.
