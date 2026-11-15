<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **A missing-AWS-credentials error under litellm's catch-all classes is permanent** — "Unable to locate credentials", "Unable to locate authorization token", partial credentials, a missing config profile and an expired SSO session raised as `APIConnectionError`, `APIError` or `InternalServerError` were retried three times and, in a routing run, written as failed rows call after call. `is_retryable` now returns False for them, so the retry wrappers fail at the first attempt and `model routing` stops with `permanent_error`.
