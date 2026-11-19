<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`report model-compare` no longer calls two-arm runs baseline-only when no question is scored in both arms.**
  The limitation now names the baseline and treatment arms and says the delta is unavailable
  because no question has scores in both arms. Runs with no treatment arm keep the old text (#965).
