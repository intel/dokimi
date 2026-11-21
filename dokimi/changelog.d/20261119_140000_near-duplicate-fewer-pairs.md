<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Changed

- **`catalog check` compares about 7x fewer pairs of descriptions for near-duplicates** — a positional bound on the rarest shared word drops candidate pairs that cannot reach a Jaccard similarity of 0.5, so a 5000-skill catalog-like corpus compares 39928 pairs (0.3% of all pairs) instead of 283510 (2.3%). The findings and their order are the same. The pairs compared still grow about 17x for 4x the skills, so the pass is quadratic with a small constant.
