<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo scan --baseline` refuses a deeply nested file as an input error (exit 2)**, not a `RecursionError` traceback with exit 5 (#851).
- **`benchmark preflight`, `run` and `batch` refuse an unreadable `file:` key of the configured provider on a `vertex_ai/` or `bedrock/` route (exit 2).** Every call reads that key, so they passed a run that failed in the first model call. Fix the file or unset the variable.
- **`catalog check --fail-on-code X --ignore X` is no longer refused for a code the scan mode never reports (exit 0, was 2).** `--ignore` filters first, so a code in both flags is not checked.
