<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`repo run`: a Copilot shell command's `rc` is its `shellExecution.exitCode` only.** A result that carries only
  `success` no longer gets `rc` `0` or `1` from it, so it is `null`. `failed_commands` and `first_useful` read this `rc`,
  so for such a command they now count neither a failure nor a useful result where they counted one before. Results
  that carry `shellExecution.exitCode` are unchanged. Refs #807.
