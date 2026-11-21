<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog novelty` summary: a ranking by new share per 1k tokens.** The sweep summary lists the
  measured skills by how much of each one the model did not already know, per 1k tokens of the
  skill (top ten in text; every skill in the `--summary-out` JSON as `ranking`, one entry per skill
  from its latest ok row).
