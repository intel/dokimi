<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`repo budget` no longer compares every pair of skills for near-duplicate descriptions** — equal word sets are grouped and the rest are found through a prefix filter whose candidates are checked at once (so shared boilerplate keeps no false pairs), and skill names are counted once, so 4000 skills with rare description words take about 0.02 s instead of 3.4 s; a word that most descriptions share, such as `common`, makes the pass compare nearly every pair again. The pairs are streamed to the one sorted list the pass builds, so a catalog of identical descriptions holds that list once, not three more full-size pair collections. The `duplicate_skills` list, its order and its values are the same.
