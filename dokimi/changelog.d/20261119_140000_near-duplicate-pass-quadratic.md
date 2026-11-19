<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Documentation

- **The `catalog check` near-duplicate pass is documented as quadratic with a small constant** — its prefix filter compares about 2.3% of all pairs of skills (18166 pairs at 1250 skills, 283510 at 5000), so the pairs compared grow about 15.6x for 4x the skills. A size filter and a positional filter keep every finding but cut the pairs at 5000 skills only to 0.3% and leave the growth at about 17x, so the filter is unchanged and the findings are the same.
