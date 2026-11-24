<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Added

- **`catalog check` notes `similar-skills-exist`** — two skills whose descriptions share 40% to 50% of their words (Jaccard, at least three shared words) under different names get a note on both, since they may be one skill under two names. Pairs at 50% or more keep the existing `description-near-duplicate` warning and get no note. Token-free: no model call, no new dependency. Ignore it with `--ignore similar-skills-exist`.
