<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- A Copilot command completion with only a `success` flag keeps its inferred `rc` of 0 or 1 and records
  `rc_from: "success"`. Failed-command counts and first-useful measurements retain the completion outcome.
  An install or removal choice requires the actual shell exit code. Refs #807.
