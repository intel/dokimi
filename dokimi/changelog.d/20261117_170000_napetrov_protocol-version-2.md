<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`dokimi.awareness` protocol is version 2**: `family_tested` (the multiplier the correction ran over)
  is a protocol input, so a result corrected over a different number of tests is no longer comparable
  with one that was not. Version 1 digests cannot be compared with version 2. A judge that
  recorded exactly one snapshot resolves an alias judge (`opus`) to it; the rollup and scorecard cells
  carry `judge_resolved_models`.
