<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`evaluate --custom-questions` with an empty, non-JSON or non-UTF-8 file exits 2** — it ended in a `JSONDecodeError` or `UnicodeDecodeError` traceback (exit 1) after the output directories were created; it now stops before any of that with `Error: --custom-questions <path>: <reason>` (exit 2). A `--questions-from` file that is not UTF-8 or JSON stops the same way, `Error: --questions-from <path>: <reason>`. A golden set found under `--questions-root` and the commands that read `--questions` are unchanged (#576).
