<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog novelty` summary: a ranking by new share.** The sweep summary sorts measured skills
  by the share of each skill the model did not already know. Ties favor the smaller token count,
  then sort by skill key. Text shows the top ten and `--summary-out` JSON lists the full ranking,
  with one entry per skill from its latest successful measurement.
