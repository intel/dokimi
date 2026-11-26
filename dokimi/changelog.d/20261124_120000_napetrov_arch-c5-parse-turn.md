<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`tools preference` reads its reply through the shared chat-response parser.** Internal
  refactor with no flag changes. A well-formed dictionary tool call now keeps its
  function name. The elapsed time uses a monotonic clock. The probe still treats
  a reply with no choices as an item error.
