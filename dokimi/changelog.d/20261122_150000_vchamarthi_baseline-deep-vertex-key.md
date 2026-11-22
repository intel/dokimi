<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo scan --baseline` refuses a deeply nested file as an input error (exit 2)**, not a `RecursionError` traceback with exit 5 (#851).
- **`benchmark preflight` refuses an unreadable `file:` key of the configured provider on a `vertex_ai/` or `bedrock/` route.** Every call reads that key, so preflight passed a run that failed in the first model call.
