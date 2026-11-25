<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`commands tools`: the tool-choice probe reads its reply through the shared chat-response
  parser.** Internal refactor; no flag or output changes. One difference: a tool call given as
  a dict (not the object a provider SDK returns) now keeps its function name instead of
  reading as `""`. The probe still treats a reply with no choices as an item error.
