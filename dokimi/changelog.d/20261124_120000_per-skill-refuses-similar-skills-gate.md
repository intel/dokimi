<!--
Copyright (C) 2026 Intel Corporation
SPDX-License-Identifier: Apache-2.0
-->

### Fixed

- **`catalog check --per-skill --fail-on-code similar-skills-exist` is refused (exit 2), not passed with exit 0.** `--per-skill` does not compare descriptions across skills, so the code is never reported; drop `--per-skill` to gate on it.
