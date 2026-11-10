<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`evaluate --custom-questions <directory>` and `trace prefilter` with a guidance file it cannot read exit 2** — a directory given as `--custom-questions` ended in an `IsADirectoryError` traceback (exit 1) after the output directories were created; it now stops before any of that with `Error: --custom-questions <path>: not a file` (exit 2), as does a file it cannot read (`Error: --custom-questions <path>: <reason>`). A guidance file for `trace prefilter` that is missing, a directory or unreadable exited 1 with `✗ cannot read <path>`; it now exits 2 with `Error: guidance <path>: <reason>`. An unreadable session keeps its rule: reported, the rest read, exit 1 (#530).
